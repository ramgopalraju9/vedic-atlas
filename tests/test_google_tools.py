"""Gmail + Calendar tools: policies, parsing, auth, adapters (fake HTTP), skills, the draft->send safety, privacy."""

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from pathlib import Path

import pytest

from domain.entities.agent_context import AgentContext
from domain.entities.calendar_event import CalendarEvent
from domain.entities.control_decision import ControlDecision
from domain.entities.mail_message import MailDraft, MailMessage
from domain.policies import calendar_policy as cal
from domain.policies import mail_policy as mail
from domain.policies.dispatch_policy import Route, resolve
from exceptions.exception import AppException, ToolUnavailableError
from service.mail.draft_outbox import DraftOutbox
from service.skills.builtin.calendar_agenda import CalendarAgendaSkill
from service.skills.builtin.gmail import GmailDraftSkill, GmailReadSkill, GmailSearchSkill, GmailSendSkill
from service.skills.registry import SkillRegistry
from service.skills.skill_runner import SkillRunner
from tpa.filestore.yaml_tool_manifest_store import YamlToolManifestStore
from tpa.online.google import consent
from tpa.online.google.calendar_client import GoogleCalendarClient, to_event
from tpa.online.google.gmail_client import GmailClient
from tpa.online.google.gmail_parser import body_text, html_to_text, to_message
from tpa.online.google.google_auth import GoogleAuth

MANIFESTS = {m.name: m for m in YamlToolManifestStore().load_all()}
GOOGLE = ("gmail_search", "gmail_read", "gmail_draft", "gmail_send", "calendar_agenda")


def run(coro):
    return asyncio.run(coro)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


# ---- fakes -----------------------------------------------------------------------------------

class FakeHttp:
    """Records requests; `routes` maps a URL substring to a payload or an exception."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []

    def _answer(self, method, url, **kw):
        self.requests.append((method, url, kw))
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value(kw) if callable(value) else value
        raise AssertionError(f"unexpected request {method} {url}")

    async def get(self, url, *, category, params=None, headers=None):
        return self._answer("GET", url, params=params, headers=headers)

    async def post_json(self, url, *, category, json, headers=None):
        return self._answer("POST", url, json=json, headers=headers)

    async def post_form(self, url, *, category, data, headers=None):
        return self._answer("FORM", url, data=data, headers=headers)


class FakeMail:
    def __init__(self, found=(), message=None, send_error=None):
        self.found, self.message, self.send_error = list(found), message, send_error
        self.searches, self.drafts, self.sent = [], [], []

    async def search(self, query, limit=5):
        self.searches.append((query, limit))
        return self.found[:limit]

    async def get(self, message_id):
        return self.message

    async def create_draft(self, to, subject, body):
        d = MailDraft(id=f"d{len(self.drafts) + 1}", to=to, subject=subject, body=body)
        self.drafts.append(d)
        return d

    async def send_draft(self, draft_id):
        if self.send_error:
            raise self.send_error
        self.sent.append(draft_id)


class FakeCalendar:
    def __init__(self, events=()):
        self.events, self.windows = list(events), []

    async def list_events(self, start, end, limit=20):
        self.windows.append((start, end))
        return self.events


def msg(sender="Priya Rao", addr="priya@example.com", subject="Lunch tomorrow?", **kw):
    return MailMessage(id=kw.pop("id", "m1"), sender_name=sender, sender_address=addr, subject=subject, **kw)


def ctx(session="s1"):
    c = AgentContext(user_message="write that the report is ready and the meeting moved")
    c.session_id = session
    return c


NOW = datetime(2026, 10, 8, 14, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))   # a Thursday


# ---- manifests -------------------------------------------------------------------------------

def test_the_five_tools_load_and_keep_the_users_content_private():
    for name in GOOGLE:
        m = MANIFESTS[name]
        assert m.private and m.requires_online and "oauth2.googleapis.com" in m.hosts
    assert MANIFESTS["gmail_send"].destructive and MANIFESTS["gmail_send"].params == ()
    assert MANIFESTS["gmail_read"].returns == "document" and MANIFESTS["gmail_read"].reply_mode == "llm"
    assert all(MANIFESTS[n].reply_mode == "template" for n in ("gmail_search", "gmail_draft", "gmail_send", "calendar_agenda"))


def test_a_send_never_joins_a_multi_call_and_a_draft_does():
    calls = lambda *names: ControlDecision(calls=tuple({"tool": n, "args": a} for n, a in names), needs_live_data=True)  # noqa: E731
    both = calls(("gmail_send", {}), ("get_weather", {}))
    assert resolve(both, MANIFESTS).route is Route.CLARIFY
    assert resolve(calls(("gmail_send", {})), MANIFESTS).route is Route.TOOLS
    ok = calls(("gmail_search", {"unread_only": True}), ("calendar_agenda", {"date_offset": 0}))
    assert resolve(ok, MANIFESTS).route is Route.TOOLS


# ---- mail policy -----------------------------------------------------------------------------

def test_clean_text_flattens_untrusted_text():
    assert mail.clean_text("Hi\n\n  there\x00‮!  ") == "Hi there !"
    assert len(mail.clean_text("x" * 500, 50)) == 50


@pytest.mark.parametrize("value,ok", [("a@b.co", True), ("first.last+tag@sub.example.com", True), ("Priya", False),
                                      ("a@b", False), ("a b@c.com", False), ("a@b.com, c@d.com", False), ("", False)])
def test_is_address(value, ok):
    assert mail.is_address(value) is ok


def test_a_name_resolves_only_when_exactly_one_address_matches():
    a, b = msg(), msg(sender="Priya Nair", addr="priya.nair@work.com", id="m2")
    assert mail.pick_address("Priya Rao", [a, a, b]) == ["priya@example.com"]
    assert sorted(mail.pick_address("Priya", [a, b])) == ["priya.nair@work.com", "priya@example.com"]
    assert mail.pick_address("Sam", [a, b]) == []
    assert mail.pick_address("", [a]) == []
    to_only = MailMessage(id="m3", recipients=("sam.k@example.com",))
    assert mail.pick_address("sam", [to_only]) == ["sam.k@example.com"]


def test_query_term_cannot_add_operators():
    assert mail.quote_for_query('a" OR in:anywhere \\') == '"a OR in:anywhere "'


def test_spoken_list_names_sender_and_subject_and_caps_at_five():
    many = [msg(sender=f"S{i}", subject=f"Sub{i}") for i in range(8)]
    text = mail.spoken_list(many)
    assert text.startswith("5 emails.") and "S0: Sub0" in text and "S5" not in text
    assert mail.spoken_list([], unread_only=True) == "You have no unread emails."
    assert mail.spoken_list([msg(sender="", addr="sam@x.com", subject="")]).endswith("sam: no subject.")


# ---- calendar policy -------------------------------------------------------------------------

@pytest.mark.parametrize("offset,days", [(-1, 1), (7, 1), (True, 1), ("1", 1), (None, 1), (0, 0), (0, 8), (0, True)])
def test_window_rejects_out_of_range(offset, days):
    with pytest.raises(ValueError):
        cal.validate_window(offset, days)


def test_window_is_local_midnight_bounds_and_labels_name_the_day():
    start, end = cal.window(NOW, 1, 2)
    assert (start.day, start.hour, end.day) == (9, 0, 11) and start.tzinfo is not None
    assert cal.day_label(NOW, 0, 1) == "today" and cal.day_label(NOW, 1, 1) == "tomorrow"
    assert cal.day_label(NOW, 2, 1) == "on Saturday"
    assert cal.day_label(NOW, 0, 7) == "in the next 7 days"


def test_clock_and_spoken_agenda():
    assert cal.clock(NOW.replace(hour=10, minute=0)) == "10 AM" and cal.clock(NOW.replace(hour=15, minute=30)) == "3:30 PM"
    assert cal.clock(NOW.replace(hour=0, minute=5)) == "12:05 AM"
    ev = [CalendarEvent("1", "Standup", NOW.replace(hour=10, minute=0)),
          CalendarEvent("2", "Diwali", NOW.replace(hour=0, minute=0), all_day=True)]
    assert cal.spoken_agenda(ev, "today") == "You have 2 events today: Standup at 10 AM, Diwali, all day."
    assert cal.spoken_agenda([], "tomorrow") == "Nothing on your calendar tomorrow."
    many = [CalendarEvent(str(i), f"E{i}", NOW.replace(hour=8 + i)) for i in range(7)]
    assert cal.spoken_agenda(many, "today").endswith("and 2 more.")
    assert cal.spoken_agenda(ev[:1], "this week", multi_day=True).startswith("You have 1 event this week: Thursday Standup")


# ---- Gmail payload parsing -------------------------------------------------------------------

def test_body_prefers_plain_strips_quotes_and_caps():
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"data": b64("<p>HTML only</p>")}},
        {"mimeType": "text/plain", "body": {"data": b64("Sure, 1pm works.\n\nOn Mon, 5 Oct 2026, Sam wrote:\n> old text\nmore old")}},
    ]}
    assert body_text(payload) == "Sure, 1pm works."
    assert len(body_text({"mimeType": "text/plain", "body": {"data": b64("word " * 2000)}}, limit=100)) <= 100


def test_html_only_mail_is_reduced_to_text_without_scripts():
    html = "<html><head><title>t</title><style>p{}</style></head><body><p>Hello <b>there</b></p><script>evil()</script><div>Bye</div></body></html>"
    assert "evil" not in html_to_text(html) and "Hello there" in html_to_text(html)
    payload = {"mimeType": "text/html", "body": {"data": b64(html)}}
    assert body_text(payload) == "Hello there\n\nBye" or body_text(payload).split() == ["Hello", "there", "Bye"]


def test_to_message_reads_headers_labels_and_date():
    item = {"id": "abc", "snippet": "hi  there", "internalDate": "1790000000000", "labelIds": ["INBOX", "UNREAD"],
            "payload": {"headers": [{"name": "From", "value": "Priya Rao <Priya@Example.com>"},
                                    {"name": "To", "value": "me@x.com, Sam <sam@y.com>"},
                                    {"name": "Subject", "value": "Lunch?"}]}}
    m = to_message(item)
    assert (m.id, m.sender_name, m.sender_address, m.subject, m.unread) == ("abc", "Priya Rao", "Priya@Example.com", "Lunch?", True)
    assert m.recipients == ("me@x.com", "sam@y.com") and m.snippet == "hi there" and m.received_at.tzinfo is not None
    assert m.body == ""


# ---- GoogleAuth ------------------------------------------------------------------------------

def make_auth(http, secrets=("id", "secret", "refresh"), clock=None):
    kw = {"clock": clock} if clock else {}
    return GoogleAuth(http, client_id=lambda: secrets[0], client_secret=lambda: secrets[1], refresh_token=lambda: secrets[2], **kw)


def test_access_token_is_cached_until_shortly_before_expiry():
    http = FakeHttp({"oauth2.googleapis.com": {"access_token": "tok1", "expires_in": 3600}})
    t = [0.0]
    auth = make_auth(http, clock=lambda: t[0])
    assert run(auth.access_token()) == "tok1" and run(auth.access_token()) == "tok1"
    assert len(http.requests) == 1 and http.requests[0][0] == "FORM"
    assert http.requests[0][2]["data"]["grant_type"] == "refresh_token"
    t[0] = 3600 - 30
    run(auth.access_token())
    assert len(http.requests) == 2
    assert run(auth.headers()) == {"Authorization": "Bearer tok1"}


def test_missing_secrets_mean_not_set_up_and_make_no_request():
    http = FakeHttp({})
    auth = make_auth(http, secrets=("id", "secret", ""))
    assert auth.missing() == ["refresh token"] and not auth.is_configured()
    with pytest.raises(ToolUnavailableError) as e:
        run(auth.access_token())
    assert e.value.not_configured and "refresh token" in e.value.message and http.requests == []


def test_a_refused_refresh_token_means_sign_in_again_not_a_service_outage():
    http = FakeHttp({"oauth2": ToolUnavailableError("t", "google", "HTTP 400", status=400)})
    with pytest.raises(ToolUnavailableError) as e:
        run(make_auth(http).access_token())
    assert e.value.not_configured and "google_auth.py" in e.value.message
    http = FakeHttp({"oauth2": ToolUnavailableError("t", "google", "HTTP 503", status=503)})
    with pytest.raises(ToolUnavailableError) as e:
        run(make_auth(http).access_token())
    assert not e.value.not_configured


# ---- adapters over fake HTTP -----------------------------------------------------------------

def gmail(routes):
    http = FakeHttp({"oauth2": {"access_token": "T", "expires_in": 3600}, **routes})
    return GmailClient(http, make_auth(http)), http


def test_gmail_search_lists_then_fetches_headers_only():
    meta = lambda kw: {"id": kw["params"] and "m1", "payload": {"headers": [{"name": "From", "value": "A <a@x.com>"}, {"name": "Subject", "value": "S"}]}}  # noqa: E731
    client, http = gmail({"/messages/m1": meta, "/messages": {"messages": [{"id": "m1"}]}})
    found = run(client.search("in:inbox", limit=3))
    assert [m.subject for m in found] == ["S"]
    listing = [r for r in http.requests if r[1].endswith("/messages")][0]
    assert listing[2]["params"] == {"q": "in:inbox", "maxResults": 3} and listing[2]["headers"] == {"Authorization": "Bearer T"}
    one = [r for r in http.requests if r[1].endswith("/messages/m1")][0]
    assert one[2]["params"]["format"] == "metadata"


def test_gmail_search_with_no_hits_makes_no_second_request():
    client, http = gmail({"/messages": {"resultSizeEstimate": 0}})
    assert run(client.search("x")) == [] and len([r for r in http.requests if r[0] == "GET"]) == 1


def test_create_draft_builds_a_real_message_and_send_posts_only_the_draft_id():
    client, http = gmail({"/drafts/send": {"id": "sent"}, "/drafts": {"id": "d9"}})
    d = run(client.create_draft("sam@x.com", "Report", "It is ready."))
    assert d.id == "d9"
    raw = [r for r in http.requests if r[1].endswith("/drafts")][0][2]["json"]["message"]["raw"]
    parsed = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    assert parsed["To"] == "sam@x.com" and parsed["Subject"] == "Report" and "It is ready." in parsed.get_payload(decode=True).decode()
    run(client.send_draft("d9"))
    assert [r for r in http.requests if r[1].endswith("/drafts/send")][0][2]["json"] == {"id": "d9"}


def test_a_header_injection_attempt_cannot_create_a_draft():
    client, http = gmail({"/drafts": {"id": "d9"}})
    with pytest.raises(ValueError):
        run(client.create_draft("a@b.com\nBcc: evil@x.com", "s", "b"))
    assert not [r for r in http.requests if r[1].endswith("/drafts")]


def test_calendar_client_parses_timed_all_day_and_drops_cancelled():
    items = {"items": [
        {"id": "1", "summary": "Standup", "start": {"dateTime": "2026-10-08T10:00:00+05:30"}, "end": {"dateTime": "2026-10-08T10:15:00+05:30"}},
        {"id": "2", "summary": "Holiday", "start": {"date": "2026-10-08"}, "end": {"date": "2026-10-09"}},
        {"id": "3", "summary": "Gone", "status": "cancelled", "start": {"dateTime": "2026-10-08T11:00:00+05:30"}},
        {"id": "4", "start": {}},
    ]}
    http = FakeHttp({"oauth2": {"access_token": "T"}, "calendar/v3": items})
    client = GoogleCalendarClient(http, make_auth(http))
    start = datetime.now().astimezone().replace(year=2026, month=10, day=8, hour=0, minute=0, second=0, microsecond=0)
    events = run(client.list_events(start - timedelta(days=1), start + timedelta(days=2)))
    assert [e.title for e in events] == ["Standup", "Holiday"] and events[1].all_day and not events[0].all_day
    params = [r for r in http.requests if "calendar" in r[1]][0][2]["params"]
    assert params["singleEvents"] == "true" and params["orderBy"] == "startTime"
    assert to_event({"id": "x", "status": "cancelled", "start": {"date": "2026-10-08"}}) is None


# ---- skills ----------------------------------------------------------------------------------

def test_search_defaults_to_the_inbox_and_adds_unread():
    fm = FakeMail([msg()])
    sk = GmailSearchSkill(fm, MANIFESTS["gmail_search"])
    res = run(sk.execute(ctx(), unread_only=True))
    assert fm.searches == [("is:unread in:inbox", 5)] and res.success
    assert res.metadata["spoken"] == "1 unread email. Priya Rao: Lunch tomorrow?."
    run(sk.execute(ctx(), query="from:priya"))
    assert fm.searches[-1][0] == "from:priya"


def test_search_failures_become_plain_speech_not_a_stack_trace():
    class Down(FakeMail):
        async def search(self, q, limit=5):
            raise ToolUnavailableError("g", "mail", "down", not_configured=True)

    res = run(GmailSearchSkill(Down(), MANIFESTS["gmail_search"]).execute(ctx()))
    assert not res.success and res.metadata["spoken"] == "I can't check your email yet because it isn't set up."


def test_read_returns_a_document_and_its_spoken_fallback_never_carries_the_body():
    body = "SECRET-BODY " * 100
    fm = FakeMail([msg()], message=msg(body=body, received_at=NOW))
    res = run(GmailReadSkill(fm, MANIFESTS["gmail_read"]).execute(ctx(), query="from:priya"))
    assert res.success and "SECRET-BODY" in res.output and res.output.startswith("EMAIL from Priya Rao <priya@example.com>")
    assert "SECRET-BODY" not in res.metadata["spoken"] and "Lunch tomorrow?" in res.metadata["spoken"]
    assert fm.searches == [("from:priya", 1)]
    none = run(GmailReadSkill(FakeMail([]), MANIFESTS["gmail_read"]).execute(ctx()))
    assert not none.success and "couldn't find" in none.metadata["spoken"]


def draft_skills(fm=None, outbox=None):
    fm = fm or FakeMail([msg()])
    outbox = outbox or DraftOutbox()
    return fm, outbox, GmailDraftSkill(fm, MANIFESTS["gmail_draft"], outbox), GmailSendSkill(fm, MANIFESTS["gmail_send"], outbox)


def test_drafting_to_a_name_resolves_one_address_reads_it_back_and_sends_nothing():
    fm, outbox, draft, _ = draft_skills()
    res = run(draft.execute(ctx(), to="Priya", subject="Running late", body="I'll be ten minutes late."))
    assert res.success and fm.sent == [] and fm.drafts[0].to == "priya@example.com"
    spoken = res.metadata["spoken"]
    assert "priya@example.com" in spoken and "Running late" in spoken and "ten minutes late" in spoken and "send it" in spoken
    assert outbox.peek("s1").id == "d1"
    q = fm.searches[0][0]
    assert q == 'from:"Priya" OR to:"Priya"'


@pytest.mark.parametrize("found,needle", [
    ([], "couldn't find an email address for Sam"),
    ([msg(), msg(sender="Sam A", addr="sam.a@x.com", id="2"), msg(sender="Sam B", addr="sam.b@x.com", id="3")], "more than one address for Sam"),
])
def test_an_unknown_or_ambiguous_name_asks_and_creates_no_draft(found, needle):
    fm, outbox, draft, _ = draft_skills(FakeMail(found))
    res = run(draft.execute(ctx(), to="Sam", subject="s", body="b"))
    assert not res.success and needle in res.metadata["spoken"] and fm.drafts == [] and outbox.peek("s1") is None


def test_a_literal_address_is_used_as_is_without_a_lookup_and_an_empty_body_is_refused():
    fm, _, draft, _ = draft_skills()
    assert run(draft.execute(ctx(), to="sam@x.com", subject="s", body="hello")).success
    assert fm.searches == [] and fm.drafts[0].to == "sam@x.com"
    empty = run(draft.execute(ctx(), to="sam@x.com", subject="s", body="  "))
    assert not empty.success and "What should the email say" in empty.metadata["spoken"]
    assert run(draft.execute(ctx(), to="", subject="s", body="b")).metadata["spoken"] == "Who should I send it to?"


def test_send_sends_exactly_the_draft_that_was_read_back_then_nothing_more():
    fm, outbox, draft, send = draft_skills()
    run(draft.execute(ctx(), to="sam@x.com", subject="Report", body="Ready."))
    res = run(send.execute(ctx()))
    assert res.success and fm.sent == ["d1"] and res.metadata["spoken"] == "Sent to sam@x.com: Report."
    again = run(send.execute(ctx()))
    assert not again.success and fm.sent == ["d1"] and "no draft" in again.metadata["spoken"].lower()


def test_send_with_no_draft_sends_nothing():
    fm, _, _, send = draft_skills()
    res = run(send.execute(ctx()))
    assert not res.success and fm.sent == [] and "write one first" in res.metadata["spoken"]


def test_a_newer_draft_replaces_the_older_one_and_a_failed_send_is_not_retried():
    fm, outbox, draft, send = draft_skills()
    run(draft.execute(ctx(), to="a@x.com", subject="one", body="1"))
    run(draft.execute(ctx(), to="b@x.com", subject="two", body="2"))
    fm.send_error = ToolUnavailableError("g", "mail", "timeout")
    res = run(send.execute(ctx()))
    assert not res.success and "Gmail drafts" in res.metadata["spoken"] and fm.sent == []
    fm.send_error = None
    assert not run(send.execute(ctx())).success and fm.sent == []     # the draft was consumed: no accidental second attempt


def test_the_outbox_expires_and_is_scoped_to_the_session():
    clock = [NOW]
    box = DraftOutbox(ttl_sec=600, now=lambda: clock[0])
    box.put(MailDraft("d", "a@x.com", "s", "b"), "s1")
    assert box.peek("s2") is None and box.peek("s1").id == "d"
    clock[0] = NOW + timedelta(seconds=601)
    assert box.peek("s1") is None and box.take("s1") is None


def test_calendar_skill_reads_the_window_and_rejects_a_bad_offset_in_plain_speech():
    fc = FakeCalendar([CalendarEvent("1", "Dentist", NOW.replace(hour=15, minute=30))])
    sk = CalendarAgendaSkill(fc, MANIFESTS["calendar_agenda"], now=lambda: NOW)
    res = run(sk.execute(ctx(), date_offset=1))
    assert res.metadata["spoken"] == "You have 1 event tomorrow: Dentist at 3:30 PM."
    start, end = fc.windows[0]
    assert start.date().isoformat() == "2026-10-09" and end - start == timedelta(days=1) and start.utcoffset() == NOW.utcoffset()
    default = run(sk.execute(ctx()))
    assert "today" in default.metadata["spoken"]
    bad = run(sk.execute(ctx(), date_offset=9))
    assert not bad.success and "0 to 6 days" in bad.metadata["spoken"] and len(fc.windows) == 2


# ---- privacy ---------------------------------------------------------------------------------

def test_a_private_skills_output_never_reaches_hooks_or_skill_results():
    seen = []

    class Hooks:
        async def fire(self, event, **kw):
            seen.append(kw)
            return []

    fm = FakeMail([msg(subject="Salary review")])
    registry = SkillRegistry()
    registry.register(GmailSearchSkill(fm, MANIFESTS["gmail_search"]))
    c = ctx()
    res = run(SkillRunner(registry, Hooks()).execute_skill(c, "gmail_search"))
    assert "Salary review" in res.output                                   # the user still hears it
    assert all("Salary" not in str(k) for k in seen) and c.skill_results[0]["output"] is None


def test_a_public_tool_keeps_its_output_in_skill_results():
    from service.skills.manifest_skill import ManifestSkill
    from domain.entities.skill_result import SkillResult

    class Public(ManifestSkill):
        async def run(self, ctx, **p):
            return SkillResult("get_weather", True, output="31 degrees")

    registry = SkillRegistry()
    registry.register(Public(MANIFESTS["get_weather"]))
    c = ctx()
    run(SkillRunner(registry).execute_skill(c, "get_weather"))
    assert c.skill_results[0]["output"] == "31 degrees"


# ---- consent helpers -------------------------------------------------------------------------

def test_auth_url_asks_for_offline_access_least_scopes_and_pkce():
    verifier, challenge = consent.pkce_pair()
    url = consent.build_auth_url("cid", "http://127.0.0.1:5000", "st", challenge)
    from urllib.parse import parse_qs, urlparse
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["access_type"] == "offline" and q["prompt"] == "consent" and q["code_challenge_method"] == "S256"
    assert q["state"] == "st" and q["redirect_uri"] == "http://127.0.0.1:5000" and q["client_id"] == "cid"
    assert set(q["scope"].split()) == set(consent.SCOPES)
    assert not any(s.endswith(("gmail.modify", "gmail.send", "calendar")) or "mail.google.com" in s for s in consent.SCOPES)
    expect = base64.urlsafe_b64encode(__import__("hashlib").sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expect


def test_refresh_token_is_accepted_only_with_every_permission_granted():
    ok = {"refresh_token": "r", "scope": " ".join(consent.SCOPES)}
    assert consent.refresh_token_from(ok) == "r"
    with pytest.raises(ToolUnavailableError):
        consent.refresh_token_from({"refresh_token": "r", "scope": consent.SCOPES[0]})
    with pytest.raises(ToolUnavailableError):
        consent.refresh_token_from({"scope": " ".join(consent.SCOPES)})


def test_upsert_env_value_keeps_other_lines_and_replaces_in_place(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("A=1\nGOOGLE_REFRESH_TOKEN=old\n# note\nB=2", encoding="utf-8")
    consent.upsert_env_value(env, "GOOGLE_REFRESH_TOKEN", "new")
    assert env.read_text(encoding="utf-8") == "A=1\nGOOGLE_REFRESH_TOKEN=new\n# note\nB=2"
    fresh = tmp_path / "fresh.env"
    consent.upsert_env_value(fresh, "K", "v")
    assert fresh.read_text(encoding="utf-8") == "K=v\n"
    consent.upsert_env_value(env, "C", "3")
    assert env.read_text(encoding="utf-8").endswith("B=2\nC=3\n")
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
