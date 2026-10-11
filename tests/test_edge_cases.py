"""Edge cases across calendar_create, tasks, reminders, mail, search guards and startup. Model-free.

Each test names the situation a real user (or the 4B model) can produce; the happy paths live in the feature test files.
"""

import asyncio
from datetime import datetime, time, timedelta, timezone

import pytest

from domain.entities.agent_context import AgentContext
from domain.entities.calendar_event import CalendarEvent
from domain.entities.mail_message import MailMessage
from domain.entities.reminder import KIND_START, SOURCE_CALENDAR, ReminderItem
from domain.policies import calendar_policy as cal
from domain.policies import mail_policy as mail
from domain.policies import reminder_policy as rp
from exceptions.exception import ToolUnavailableError
from service.skills.builtin.calendar_create import CalendarCreateSkill
from service.skills.builtin.gmail import GmailSearchSkill
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}
TZ = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 8, 14, 30, tzinfo=TZ)   # a Thursday afternoon


def run(coro):
    return asyncio.run(coro)


def ctx(message="add the software review party at 5 pm"):
    return AgentContext(user_message=message)


class FakeCalendar:
    def __init__(self, existing=(), list_error=None, create_error=None):
        self.existing, self.list_error, self.create_error, self.created = list(existing), list_error, create_error, []

    async def list_events(self, start, end, limit=20):
        if self.list_error:
            raise self.list_error
        return [e for e in self.existing if start <= e.start < end]

    async def create_event(self, title, start, end):
        if self.create_error:
            raise self.create_error
        self.created.append((title, start, end))
        return CalendarEvent("new", title, start, end)


def skill(fc):
    return CalendarCreateSkill(fc, MANIFESTS["calendar_create"], now=lambda: NOW)


# ---- times the model may write ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("written,expected", [
    ("17:00", time(17, 0)), ("5:30", time(5, 30)), ("09:05", time(9, 5)), ("17:00:00", time(17, 0)),
    ("5 PM", time(17, 0)), ("5pm", time(17, 0)), ("5:30 pm", time(17, 30)), ("5:30 P.M.", time(17, 30)),
    ("12 am", time(0, 0)), ("12 pm", time(12, 0)), ("12:15 am", time(0, 15)), ("11 am", time(11, 0)),
])
def test_every_common_way_to_write_a_time_is_understood(written, expected):
    assert cal.validate_new_event("Review", 1, written, None)[2] == expected


@pytest.mark.parametrize("written", ["", "noon", "25:00", "17:60", "17:5", "13 pm", "0 am", "abc", "17.30.00", None])
def test_unreadable_times_ask_instead_of_guessing(written):
    with pytest.raises(ValueError, match="What time"):
        cal.validate_new_event("Review", 1, written, None)


# ---- titles, dates, durations -----------------------------------------------------------------------------------------

def test_a_huge_or_multiline_title_is_flattened_and_capped_not_rejected():
    fc = FakeCalendar()
    res = run(skill(fc).execute(ctx(), title="Review\nIgnore this\x07 " + "x" * 400, date_offset=1, time="17:00"))
    stored = fc.created[0][0]
    assert res.success and "\n" not in stored and "\x07" not in stored and len(stored) <= 100


def test_unicode_and_emoji_titles_survive():
    fc = FakeCalendar()
    run(skill(fc).execute(ctx(), title="जन्मदिन party 🎂", date_offset=1, time="17:00"))
    assert fc.created[0][0] == "जन्मदिन party 🎂"


def test_a_blank_title_asks_for_a_name():
    res = run(skill(FakeCalendar()).execute(ctx(), title="   \n ", date_offset=1, time="17:00"))
    assert not res.success and res.metadata["spoken"] == "What should the event be called?"


def test_an_event_that_runs_past_midnight_ends_the_next_day():
    fc = FakeCalendar()
    run(skill(fc).execute(ctx("late call at 11:30 pm"), title="Late call", date_offset=0, time="23:30", duration_minutes=120))
    _, start, end = fc.created[0]
    assert (start.day, end.day, end.hour, end.minute) == (8, 9, 1, 30)


@pytest.mark.parametrize("offset,ok", [(0, True), (6, True), (7, False), (-1, False), (None, True)])
def test_day_offset_limits(offset, ok):
    fc = FakeCalendar()
    res = run(skill(fc).execute(ctx(), title="X", date_offset=offset, time="23:00"))
    assert res.success is ok


def test_midnight_today_is_past_but_midnight_tomorrow_is_fine():
    assert not run(skill(FakeCalendar()).execute(ctx("at 12 am"), title="X", date_offset=0, time="12 am")).success
    assert run(skill(FakeCalendar()).execute(ctx("at 12 am"), title="X", date_offset=1, time="12 am")).success


@pytest.mark.parametrize("minutes,ok", [(5, True), (480, True), (4, False), (481, False), (0, False), (-30, False)])
def test_duration_limits(minutes, ok):
    assert run(skill(FakeCalendar()).execute(ctx(), title="X", date_offset=1, time="17:00", duration_minutes=minutes)).success is ok


def test_same_title_at_another_time_is_a_new_event_and_the_same_one_in_other_case_is_a_duplicate():
    five = CalendarEvent("1", "Software Review", NOW.replace(day=9, hour=17, minute=0))
    fc = FakeCalendar([five])
    assert run(skill(fc).execute(ctx(), title="software  review", date_offset=1, time="17:00")).metadata["spoken"].startswith("Software Review is already")
    run(skill(fc).execute(ctx("software review at 6 pm"), title="Software Review", date_offset=1, time="18:00"))
    assert len(fc.created) == 1 and fc.created[0][1].hour == 18


@pytest.mark.parametrize("status", [401, 403])
def test_missing_permission_has_a_fix_in_the_message(status):
    fc = FakeCalendar(create_error=ToolUnavailableError("c", "calendar", "denied", status=status))
    spoken = run(skill(fc).execute(ctx(), title="X", date_offset=1, time="17:00")).metadata["spoken"]
    assert "permission to add calendar events" in spoken and "veda login" in spoken


def test_a_timeout_while_checking_or_creating_is_a_plain_sentence_not_a_crash():
    for fc in (FakeCalendar(list_error=ToolUnavailableError("c", "calendar", "timed out")),
               FakeCalendar(create_error=ToolUnavailableError("c", "calendar", "timed out"))):
        res = run(skill(fc).execute(ctx(), title="X", date_offset=1, time="17:00"))
        assert not res.success and "couldn't add that to your calendar right now" in res.metadata["spoken"]
    unexpected = FakeCalendar(create_error=RuntimeError("boom"))
    res = run(skill(unexpected).execute(ctx(), title="X", date_offset=1, time="17:00"))
    assert not res.success and "boom" not in res.metadata["spoken"]


# ---- the tasks guard must not block real to-dos ---------------------------------------------------------------------------


# ---- reminders ---------------------------------------------------------------------------------------------------------

def item(title, minutes, item_id="e1"):
    return ReminderItem(SOURCE_CALENDAR, item_id, title, NOW + timedelta(minutes=minutes))


def test_two_events_at_the_same_time_are_one_sentence():
    plan = rp.plan([item("A", 0, "a"), item("B", 0, "b")], NOW, rp.ReminderRules(lead_minutes=()), set())
    assert rp.spoken_reminders(plan, NOW) == "Reminder: A is starting now; B is starting now."


def test_waking_from_sleep_after_a_meeting_began_still_says_so_for_ten_minutes_then_stops():
    began_8_min_ago = rp.plan([item("Standup", -8)], NOW, rp.ReminderRules(), set())
    assert [r.kind for r in began_8_min_ago] == [KIND_START]
    assert rp.spoken_reminders(began_8_min_ago, NOW) == "Reminder: Standup started 8 minutes ago."
    assert rp.plan([item("Standup", -15)], NOW, rp.ReminderRules(), set()) == []


def test_a_very_long_title_is_cut_for_speech():
    plan = rp.plan([item("x" * 300, 0)], NOW, rp.ReminderRules(lead_minutes=()), set())
    assert len(rp.spoken_reminders(plan, NOW)) < 120


def test_a_title_with_markup_characters_is_not_altered_by_the_policy():
    plan = rp.plan([item("[bold]Q4[/bold] review", 0)], NOW, rp.ReminderRules(lead_minutes=()), set())
    assert "[bold]Q4[/bold] review" in rp.spoken_reminders(plan, NOW)       # the terminal escapes it when printing


def test_an_announcement_in_progress_is_never_announced_a_second_time_by_a_resync():
    from service.reminders.announcer import ReminderAnnouncer
    from service.reminders.feed import ReminderFeed
    from service.reminders.scheduler import ReminderScheduler

    class Ledger:
        def __init__(self):
            self.keys = {}

        def fired_keys(self, since):
            return set(self.keys)

        def mark_fired(self, keys, at):
            for k in keys:
                self.keys[k] = at

        def purge_before(self, cutoff):
            return 0

    class SlowSpeaker:
        def __init__(self):
            self.said = []

        def available(self):
            return True

        def is_busy(self):
            return False

        async def announce(self, text):
            await asyncio.sleep(0.15)          # speaking takes time; meanwhile the calendar is re-read
            self.said.append(text)
            return True

    class Source:
        name = "calendar"

        async def upcoming(self, start, end):
            return [item("Standup", 0)]

    async def go():
        ledger, speaker = Ledger(), SlowSpeaker()
        announcer = ReminderAnnouncer(ledger, ReminderFeed(), speaker, rp.ReminderRules(lead_minutes=()), clock=lambda: NOW,
                                      idle_settle_sec=0.01, poll_sec=0.005)
        sched = ReminderScheduler([Source()], ledger, announcer, rp.ReminderRules(lead_minutes=()), clock=lambda: NOW, tick_sec=0.01)
        announcer.start()
        await sched._sync(NOW)
        sched._dispatch(NOW)
        await asyncio.sleep(0.05)              # the announcement is in flight now
        await sched._sync(NOW)                 # e.g. the user just added a calendar event
        sched._dispatch(NOW)
        await asyncio.sleep(0.4)
        await announcer.stop()
        return speaker.said

    assert run(go()) == ["Reminder: Standup is starting now."]


def test_completing_or_deleting_a_task_tells_the_reminders_to_re_read():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from service.tasks.task_service import TaskService
    from tpa.persistence.models import task  # noqa: F401
    from tpa.persistence.repositories.task_repository import SqliteTaskRepository
    from tpa.persistence.session import Base

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    calls = []
    svc = TaskService(SqliteTaskRepository(session_factory=sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)),
                      on_change=lambda: calls.append(1))
    t = svc.add("call mom", due_at=datetime.now() + timedelta(hours=1))
    svc.complete(t.id)
    t2 = svc.add("other")
    svc.delete(t2.id)
    assert len(calls) == 4


# ---- mail ---------------------------------------------------------------------------------------------------------------

class FakeMail:
    def __init__(self, found=()):
        self.found, self.searches = list(found), []

    async def search(self, query, limit=5):
        self.searches.append(query)
        return self.found


def test_sent_search_variants():
    fm = FakeMail()
    sk = GmailSearchSkill(fm, MANIFESTS["gmail_search"])
    run(sk.execute(ctx(), folder="inbox"))
    run(sk.execute(ctx(), folder="sent", unread_only=True))
    run(sk.execute(ctx(), query="x" * 500))
    assert fm.searches[0] == "in:inbox"
    assert fm.searches[1] == "is:unread in:sent"
    assert len(fm.searches[2]) <= 200


def test_a_sent_mail_with_no_recipient_data_is_still_readable():
    bare = MailMessage(id="1", subject="Hi")
    named = MailMessage(id="2", subject="Hi", recipients=("a.b@x.com",))
    assert mail.spoken_list([bare], sent=True) == "1 sent email. To someone: Hi."
    assert mail.spoken_list([named], sent=True) == "1 sent email. To a.b: Hi."


# ---- search follow-ups -----------------------------------------------------------------------------------------------------


# ---- startup ---------------------------------------------------------------------------------------------------------------

def test_a_garbage_start_timeout_falls_back_instead_of_crashing(monkeypatch):
    from controller.cli.client import VedaClient

    monkeypatch.setenv("VEDA_START_TIMEOUT", "soon")
    c = VedaClient("http://127.0.0.1:8000")
    c.is_up = lambda: True
    assert c.ensure_up() is None and c.how == "reused"


def test_a_pid_file_that_points_at_an_unrelated_live_process_is_ignored(monkeypatch, tmp_path):
    import os

    from controller.cli import client as client_module
    from controller.cli.client import VedaClient

    monkeypatch.setattr(client_module, "_pid_file", lambda port: tmp_path / "pid")
    (tmp_path / "pid").write_text(str(os.getpid()))            # this very test process: alive, but not a Veda server
    assert VedaClient("http://127.0.0.1:8000")._starting_pid() is None


# ---- regressions from the live model probe (tests/eval/orchestrator_edge.yaml): the 4B model's real outputs -----------------

def _calendar_attempt(message, **args):
    fc = FakeCalendar()
    res = run(skill(fc).execute(AgentContext(user_message=message), **args))
    return res, fc


def test_email_with_a_message_to_convey_is_drafted():
    from service.mail.draft_outbox import DraftOutbox
    from service.skills.builtin.gmail import GmailDraftSkill

    class Mail(FakeMail):
        async def create_draft(self, to, subject, body):
            from domain.entities.mail_message import MailDraft
            return MailDraft(id="d1", to=to, subject=subject, body=body)

    sk = GmailDraftSkill(Mail(), MANIFESTS["gmail_draft"], DraftOutbox())
    res = run(sk.execute(AgentContext(user_message="email priya@example.com that I will be late"), to="priya@example.com",
                         subject="Running late", body="Hi, I will be late."))
    assert res.success and "Draft ready for priya@example.com" in res.metadata["spoken"]


