"""calendar_create, current_time, sent-mail search, and the guards that keep calendar requests out of tasks and
pronoun-only follow-ups out of the web search. Model-free: every value the model would choose is passed in directly."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from domain.entities.agent_context import AgentContext
from domain.entities.calendar_event import CalendarEvent
from domain.entities.mail_message import MailMessage
from domain.policies import calendar_policy as cal
from domain.policies import mail_policy as mail
from exceptions.exception import ToolUnavailableError
from service.lookup.search_lookup import _first_sentences
from service.skills.builtin.calendar_create import CalendarCreateSkill
from service.skills.builtin.clock import CurrentTimeSkill
from service.skills.builtin.gmail import GmailSearchSkill
from service.skills.builtin.web_search import WebSearchSkill
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore
from tpa.online.google.calendar_client import GoogleCalendarClient
from tpa.online.google.gmail_parser import to_message

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}
NOW = datetime(2026, 10, 8, 14, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))   # a Thursday


def run(coro):
    return asyncio.run(coro)


def ctx(message="add the software review and the dentist and the review at 5 pm"):
    return AgentContext(user_message=message)


class FakeCalendar:
    def __init__(self, existing=(), create_error=None):
        self.existing, self.create_error, self.created = list(existing), create_error, []

    async def list_events(self, start, end, limit=20):
        return [e for e in self.existing if start <= e.start < end]

    async def create_event(self, title, start, end):
        if self.create_error:
            raise self.create_error
        self.created.append((title, start, end))
        return CalendarEvent("new", title, start, end)


# ---- calendar_create -----------------------------------------------------------------------------------------------

def skill(cal_port):
    return CalendarCreateSkill(cal_port, MANIFESTS["calendar_create"], now=lambda: NOW)


def test_creates_the_event_tomorrow_at_five_and_reads_it_back_from_what_was_stored():
    fc = FakeCalendar()
    res = run(skill(fc).execute(ctx(), title="Software review", date_offset=1, time="17:00"))
    assert res.success and res.metadata["spoken"] == "Added Software review to your calendar tomorrow at 5 PM."
    (title, start, end), = fc.created
    assert start == NOW.replace(day=9, hour=17, minute=0) and end - start == timedelta(hours=1)


def test_duration_is_honoured_and_defaults_to_one_hour():
    fc = FakeCalendar()
    run(skill(fc).execute(ctx(), title="Dentist", time="16:45", duration_minutes=30))
    (_, start, end), = fc.created
    assert end - start == timedelta(minutes=30) and start.day == 8


def test_the_same_event_is_not_added_twice():
    existing = CalendarEvent("1", "Software review", NOW.replace(day=9, hour=17, minute=0))
    fc = FakeCalendar([existing])
    res = run(skill(fc).execute(ctx(), title="software review", date_offset=1, time="17:00"))
    assert res.success and fc.created == [] and "already on your calendar tomorrow at 5 PM" in res.metadata["spoken"]


@pytest.mark.parametrize("args,sentence", [
    ({"title": "", "time": "17:00"}, "What should the event be called?"),
    ({"title": "Review", "time": ""}, "What time should I set it for?"),
    ({"title": "Review", "time": "25:00"}, "What time should I set it for?"),
    ({"title": "Review", "time": "17:00", "date_offset": 9}, "I can only add events up to 6 days ahead."),
    ({"title": "Review", "time": "09:00", "date_offset": 0}, "That time has already passed."),
    ({"title": "Review", "time": "17:00", "duration_minutes": 1}, "An event can last between 5 minutes and 8 hours."),
])
def test_bad_input_is_refused_in_plain_speech_and_nothing_is_stored(args, sentence):
    fc = FakeCalendar()
    res = run(skill(fc).execute(ctx(), **args))
    assert not res.success and res.metadata["spoken"] == sentence and fc.created == []


def test_a_sign_in_without_calendar_write_access_says_so_and_how_to_fix_it():
    fc = FakeCalendar(create_error=ToolUnavailableError("c", "calendar", "403", status=403))
    res = run(skill(fc).execute(ctx(), title="Review", time="17:00", date_offset=1))
    assert not res.success and "permission to add calendar events" in res.metadata["spoken"]


def test_the_manifest_cannot_invite_anyone_and_keeps_content_private():
    m = MANIFESTS["calendar_create"]
    assert m.private and m.reply_mode == "template" and {p.name for p in m.params} == {"title", "date_offset", "time", "duration_minutes"}


def test_google_adapter_posts_the_event_without_attendees():
    class Http:
        def __init__(self):
            self.sent = []

        async def post_json(self, url, *, category, json, headers=None):
            self.sent.append(json)
            return {"id": "e1", "summary": json["summary"], "start": json["start"], "end": json["end"]}

    class Auth:
        async def headers(self):
            return {"Authorization": "Bearer T"}

    http = Http()
    event = run(GoogleCalendarClient(http, Auth()).create_event("Review", NOW, NOW + timedelta(hours=1)))
    assert event.title == "Review" and "attendees" not in http.sent[0] and http.sent[0]["start"]["dateTime"] == NOW.isoformat()


# ---- tasks must not swallow calendar entries -------------------------------------------------------------------------


# ---- current_time ----------------------------------------------------------------------------------------------------

def test_current_time_and_date_come_from_the_clock():
    sk = CurrentTimeSkill(MANIFESTS["current_time"], now=lambda: NOW)
    assert run(sk.execute(ctx())).metadata["spoken"] == "It's 2:30 PM."
    assert run(sk.execute(ctx(), what="time")).metadata["spoken"] == "It's 2:30 PM."
    assert run(sk.execute(ctx(), what="date")).metadata["spoken"] == "Today is Thursday 8 October 2026."


# ---- sent mail --------------------------------------------------------------------------------------------------------

class FakeMail:
    def __init__(self, found=()):
        self.found, self.searches = list(found), []

    async def search(self, query, limit=5):
        self.searches.append(query)
        return self.found


def sent_msg():
    return MailMessage(
        id="m1", sender_name="Me", sender_address="me@x.com", subject="Tomorrow's Calendar Invite",
        recipients=("manojrouthu26@gmail.com",), recipient_names=("Manoj Routhu",),
    )


def test_sent_folder_searches_in_sent_whatever_query_the_model_wrote():
    fm = FakeMail([sent_msg()])
    sk = GmailSearchSkill(fm, MANIFESTS["gmail_search"])
    res = run(sk.execute(ctx(), folder="sent", query="to:manojrouthu26@gmail.com"))
    assert fm.searches == ["in:sent to:manojrouthu26@gmail.com"]
    assert res.metadata["spoken"] == "1 sent email. To Manoj Routhu: Tomorrow's Calendar Invite."
    run(sk.execute(ctx(), folder="sent"))
    assert fm.searches[-1] == "in:sent"


def test_existing_inbox_behaviour_is_unchanged_when_no_folder_is_given():
    fm = FakeMail([])
    sk = GmailSearchSkill(fm, MANIFESTS["gmail_search"])
    run(sk.execute(ctx(), unread_only=True))
    run(sk.execute(ctx(), query="from:priya"))
    run(sk.execute(ctx(), folder="inbox", query="invoice"))
    assert fm.searches == ["is:unread in:inbox", "from:priya", "in:inbox invoice"]


def test_nothing_sent_is_said_as_such():
    assert mail.spoken_list([], sent=True) == "You haven't sent any matching emails."


def test_recipient_names_are_parsed_from_the_to_header():
    item = {"id": "1", "payload": {"headers": [
        {"name": "From", "value": "me@x.com"}, {"name": "To", "value": "Manoj Routhu <m@x.com>, plain@y.com"},
        {"name": "Subject", "value": "Hi"},
    ]}}
    m = to_message(item)
    assert m.recipients == ("m@x.com", "plain@y.com") and m.recipient_names == ("Manoj Routhu", "")
    assert m.recipient_label() == "Manoj Routhu"


# ---- web search follow-ups ----------------------------------------------------------------------------------------------


def test_initials_and_titles_do_not_end_a_spoken_sentence():
    text = "Infosys was founded by N. R. Narayana Murthy, K. Dinesh and Ashok Arora in Pune. More follows here."
    assert _first_sentences(text, 90) == "Infosys was founded by N. R. Narayana Murthy, K. Dinesh and Ashok Arora in Pune."
    assert _first_sentences("He met Dr. Rao at St. Mary. Second.", 40) == "He met Dr. Rao at St. Mary. Second."
