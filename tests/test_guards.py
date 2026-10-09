"""The write guards: the model's tool call is checked against the user's own words before anything is written.

Born from a live probe of the 4B model (tests/eval/orchestrator_edge.yaml): it answered requests it has no tool for with a
write tool ("move my meeting to 6 pm" created an event, "delete the 5 pm meeting" created "Software review", copied from the
prompt), invented a time or an email body, and passed "it"/"there" through as a search. Each case below is a real output.
There is deliberately NO greeting check: "Hey veda!" goes to the model like everything else.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from domain.entities.agent_context import AgentContext
from domain.entities.calendar_event import CalendarEvent
from domain.entities.mail_message import MailDraft
from domain.policies import calendar_policy as cal
from domain.policies.query_policy import has_unresolved_reference
from service.mail.draft_outbox import DraftOutbox
from service.skills.builtin.calendar_create import CalendarCreateSkill
from service.skills.builtin.clock import CurrentTimeSkill
from service.skills.builtin.gmail import GmailDraftSkill
from service.skills.builtin.tasks import TasksSkill
from service.skills.builtin.web_search import WebSearchSkill
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}
NOW = datetime(2026, 10, 8, 14, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def run(coro):
    return asyncio.run(coro)


def say(message):
    return AgentContext(user_message=message)


class FakeCalendar:
    def __init__(self):
        self.created = []

    async def list_events(self, start, end, limit=20):
        return []

    async def create_event(self, title, start, end):
        self.created.append((title, start, end))
        return CalendarEvent("new", title, start, end)


def book(message, **args):
    fc = FakeCalendar()
    res = run(CalendarCreateSkill(fc, MANIFESTS["calendar_create"], now=lambda: NOW).execute(say(message), **args))
    return res, fc


# ---- calendar: add, never change -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("message,args", [
    ("move my meeting to 6 pm", {"title": "Meeting", "date_offset": 0, "time": "18:00"}),
    ("delete the 5 pm meeting from my calendar", {"title": "Software review", "date_offset": 1, "time": "17:00"}),
    ("cancel my 5 pm meeting", {"title": "Meeting", "date_offset": 0, "time": "17:00"}),
    ("reschedule the review to 4 pm", {"title": "Review", "date_offset": 0, "time": "16:00"}),
])
def test_a_request_to_change_or_delete_an_event_never_creates_one(message, args):
    res, fc = book(message, **args)
    assert not res.success and fc.created == [] and "can't move, cancel or delete" in res.metadata["spoken"]


@pytest.mark.parametrize("message", [
    "add moving day to my calendar tomorrow at 5 pm", "put the delete-old-files job on my calendar tomorrow at 5 pm",
    "schedule the review, then clear my afternoon, tomorrow at 5 pm",
])
def test_a_change_word_inside_a_real_add_request_is_allowed(message):
    res, fc = book(message, title="Moving day review job", date_offset=1, time="17:00")
    assert res.success and len(fc.created) == 1


# ---- calendar: the title and the time must be the user's ---------------------------------------------------------------------

def test_a_title_copied_from_the_prompt_example_is_not_booked():
    res, fc = book("put it on my calendar tomorrow at 5 pm", title="Software review", date_offset=1, time="17:00")
    assert not res.success and fc.created == [] and res.metadata["spoken"] == "What should the event be called?"


@pytest.mark.parametrize("message,title", [
    ("put a design review on my calendar tomorrow at 3 pm", "Design review"), ("book the dentist tomorrow at 3 pm", "Dentist appointment"),
    ("add meetings with Priya tomorrow at 3 pm", "Meeting with Priya"), ("block 2 hours tomorrow at 2 pm for deep work", "Deep work"),
])
def test_titles_built_from_the_users_words_pass(message, title):
    res, fc = book(message, title=title, date_offset=1, time="15:00")
    assert res.success, res.metadata["spoken"]


@pytest.mark.parametrize("message", [
    "put the dentist on my calendar", "then block the dentist for that time", "block the dentist for 30 minutes tomorrow morning",
])
def test_a_time_the_user_never_said_is_not_booked_even_if_the_model_made_one_up(message):
    res, fc = book(message, title="Dentist", date_offset=0, time="15:00")
    assert not res.success and res.metadata["spoken"] == "What time should I set it for?" and fc.created == []


@pytest.mark.parametrize("said", [
    "I have 5 amazing ideas", "block 30 minutes tomorrow", "good afternoon, add it", "for 90 minutes", "in 2 hours",
    "put it on my calendar", "tomorrow morning", "that time",
])
def test_things_that_are_not_a_clock_time_do_not_count_as_one(said):
    assert cal.message_states_time(said) is False


@pytest.mark.parametrize("said", [
    "at 5", "5 pm", "5pm", "5 p.m.", "17:30", "at noon", "midnight", "5 in the evening", "tonight at 8", "ten thirty",
    "quarter to six", "half past five", "five o'clock", "5 o'clock", "at five thirty", "block my calendar tomorrow at 5 pm",
])
def test_spoken_ways_to_say_a_time_count(said):
    assert cal.message_states_time(said) is True


# ---- tasks are not the calendar -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "block calendar for Manoj Routhu", "put it in my calendar", "event with Manoj", "check my calender", "add to calendar dentist",
])
def test_calendar_entries_are_recognised(title):
    assert cal.looks_like_calendar_entry(title)


@pytest.mark.parametrize("title", [
    "schedule dentist appointment", "book a meeting room", "create the event poster", "prepare for the meeting", "buy a wall planner",
    "call the bank", "schedule a meeting with Sam", "buy milk", "renew passport",
])
def test_ordinary_to_dos_are_left_alone(title):
    assert not cal.looks_like_calendar_entry(title)


def test_tasks_tool_refuses_a_calendar_entry_and_points_to_the_calendar():
    from tests.test_tasks_skill import _MANIFEST, _run, _service

    svc = _service()
    res = _run(TasksSkill(svc, _MANIFEST), action="add", title="block calendar for Manoj Routhu")
    assert not res.success and "calendar event rather than a task" in res.metadata["spoken"] and svc.list() == []


# ---- email: a draft needs something to say ------------------------------------------------------------------------------------------

class Mail:
    def __init__(self):
        self.drafts = []

    async def search(self, query, limit=5):
        return []

    async def create_draft(self, to, subject, body):
        self.drafts.append(body)
        return MailDraft(id="d1", to=to, subject=subject, body=body)


@pytest.mark.parametrize("message", ["email Priya", "send an email to priya@example.com", "can you write a mail to Priya please"])
def test_email_with_nothing_to_say_asks_instead_of_inventing_a_body(message):
    mail = Mail()
    sk = GmailDraftSkill(mail, MANIFESTS["gmail_draft"], DraftOutbox())
    res = run(sk.execute(say(message), to="Priya", subject="email", body="Hi Priya, I'll be ten minutes late."))
    assert not res.success and res.metadata["spoken"] == "What should the email say?" and mail.drafts == []


def test_email_with_a_message_to_convey_is_drafted():
    mail = Mail()
    sk = GmailDraftSkill(mail, MANIFESTS["gmail_draft"], DraftOutbox())
    res = run(sk.execute(say("email priya@example.com that I will be late"), to="priya@example.com",
                         subject="Running late", body="Hi, I will be late."))
    assert res.success and "Draft ready for priya@example.com" in res.metadata["spoken"]


# ---- search: a bare pronoun is not a query --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("where are they from", True), ("what is happening there", True), ("where is it", True), ("who founded it", True),
    ("what is it", True), ("what is that", True), ("tell me about it", True), ("WHAT IS HAPPENING THERE", True),
    ("who is her husband", False), ("how old is the Eiffel Tower", False), ("where are the founders of Infosys from", False),
    ("what is happening in Hyderabad today", False), ("capital of that country", False), ("latest news on ISRO", False), ("", False),
])
def test_unresolved_reference(query, expected):
    assert has_unresolved_reference(query) is expected


def test_web_search_asks_instead_of_searching_for_a_pronoun():
    class Boom:
        async def search(self, *a, **k):
            raise AssertionError("must not search")

    res = run(WebSearchSkill(Boom(), MANIFESTS["web_search"]).execute(say("where is it"), query="where is it"))
    assert not res.success and res.metadata["spoken"].startswith("What do you mean?")


# ---- clock: do not pass off local time as another city's ------------------------------------------------------------------------------

@pytest.mark.parametrize("message,spoken", [
    ("what time is it", "It's 2:30 PM."),
    ("what time is it in Tokyo", "It's 2:30 PM here. I can't check the time anywhere else."),
    ("what's the time in London right now", "It's 2:30 PM here. I can't check the time anywhere else."),
    ("what time is it in the morning", "It's 2:30 PM."),
    ("tell me the time in about an hour", "It's 2:30 PM."),
])
def test_the_clock_is_honest_about_other_time_zones(message, spoken):
    sk = CurrentTimeSkill(MANIFESTS["current_time"], now=lambda: NOW)
    assert run(sk.execute(say(message), what="time")).metadata["spoken"] == spoken


# ---- there is no greeting guard ------------------------------------------------------------------------------------------------------------

def test_a_greeting_is_not_intercepted_in_code():
    from domain.entities.control_decision import ControlDecision
    from domain.policies.dispatch_policy import Route, resolve

    call = ControlDecision(needs_live_data=True, calls=({"tool": "get_weather", "args": {"place": "Pune"}},))
    assert resolve(call, MANIFESTS, user_message="Hey veda!").route is Route.TOOLS      # the model's decision stands
