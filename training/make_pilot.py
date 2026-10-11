"""Writes training/datasets/pilot.jsonl: a hand-checked starter batch that exercises every category and every tool.

It exists to prove the chain end to end (validate -> leak check -> render -> split -> token counts) and to show the labeling
guide in action. Samples whose behaviour the labeling guide still leaves open carry status="provisional" and are left out of a
real build with --exclude-provisional. All names are made up.

    python -m training.make_pilot
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training.sample import Sample, dump  # noqa: E402

OUT = ROOT / "training" / "datasets" / "pilot.jsonl"
MORNING = "2026-10-09T10:15"   # a Friday morning, for "today at 6 pm" style requests

samples: list[Sample] = []


def call(tool: str, **args):
    return {"tool": tool, "args": args}


def add(cat: str, user: str, calls=(), live=None, clar=None, active="", history=(), status="final", today="2026-10-09T18:30", group=None):
    label: dict = {"needs_live_data": bool(calls) or bool(live), "calls": list(calls)}
    if clar:
        label["clarification"] = clar
        label["needs_live_data"] = False
    samples.append(Sample(
        id=f"pilot-{len(samples) + 1:03d}", category=cat, user=user, label=label, active=active, history=list(history),
        status=status, today=today, group=group or "",
    ))


# ---- explicit requests, one or two per tool, varied wording and speech-to-text style -------------------------------------------------
add("explicit", "weather in nagpur", [call("get_weather", place="Nagpur")])
add("explicit", "how's the weather today", [call("get_weather")])
add("explicit", "will it rain in pune tomorrow", [call("get_weather_forecast", place="Pune", date_offset=1)])
add("explicit", "convert two hundred dollars to rupees", [call("convert_currency", amount=200, **{"from": "USD", "to": "INR"})])
add("explicit", "what is the latest news about isro", [call("web_search", query="ISRO latest news", topic="news")])
add("explicit", "who is the current prime minister of japan", [call("web_search", query="current prime minister of Japan", topic="general")])
add("explicit", "add buy filter coffee to my tasks", [call("tasks", action="add", title="buy filter coffee")])
add("explicit", "what are my tasks", [call("tasks", action="list")])
add("explicit", "i finished the budget report", [call("tasks", action="complete", title="budget report")])
add("explicit", "remind me to call the plumber", [call("tasks", action="add", title="call the plumber")])
add("explicit", "remind me at six pm to call mom", [call("tasks", action="add", title="call mom", due_at="2026-10-09T18:00")], today=MORNING)
add("explicit", "remember my locker number is forty two", [call("remember", action="save", topic="locker number", value="42")])
add("explicit", "open spotify", [call("app_control", action="open", name="spotify")])
add("explicit", "set the volume to forty", [call("volume_control", action="set", level=40)])
add("explicit", "mute the speaker", [call("volume_control", action="mute")])
add("explicit", "how much battery is left", [call("device_status", what="battery")])
add("explicit", "check my inbox", [call("gmail_search", unread_only=True)])
add("explicit", "any mail from asha today", [call("gmail_search", query="from:asha newer_than:1d")])
add("explicit", "what mail did i send to ravi", [call("gmail_search", query="to:ravi", folder="sent")])
add("explicit", "read my last email from meera", [call("gmail_read", query="from:meera")])
add("explicit", "email karan that the report is ready",
    [call("gmail_draft", to="Karan", subject="Report ready", body="Hi Karan, the report is ready.")])
add("explicit", "what's on my calendar tomorrow", [call("calendar_agenda", date_offset=1)])
add("explicit", "what does my week look like", [call("calendar_agenda", date_offset=0, days=7)])
add("explicit", "put a project kickoff on my calendar tomorrow at ten", [call("calendar_create", title="Project kickoff", date_offset=1, time="10:00")])
add("explicit", "block two hours today at six pm for deep work",
    [call("calendar_create", title="Deep work", date_offset=0, time="18:00", duration_minutes=120)], today=MORNING)
add("explicit", "what time is it", [call("current_time", what="time")])
add("explicit", "which day is it today", [call("current_time", what="date")])

# ---- speech-to-text noise: homophones, number words, a wake phrase in front, no punctuation ------------------------------------------------
add("stt", "um what's the whether in delhi today", [call("get_weather", place="Delhi")])
add("stt", "convert five hundred rupee to dollar", [call("convert_currency", amount=500, **{"from": "INR", "to": "USD"})])
add("stt", "hey veda what is the weather in chennai right now", [call("get_weather", place="Chennai")])
add("stt", "add milk and eggs to my to do list", [call("tasks", action="add", title="milk and eggs")])

# ---- more than one thing in a sentence ---------------------------------------------------------------------------------------------------
add("multi", "weather in goa and convert fifty euros to rupees",
    [call("get_weather", place="Goa"), call("convert_currency", amount=50, **{"from": "EUR", "to": "INR"})])
add("multi", "add pay rent to my tasks and what's the weather in kochi",
    [call("tasks", action="add", title="pay rent"), call("get_weather", place="Kochi")])

# ---- follow-ups that lean on what was just done ------------------------------------------------------------------------------------------------
add("followup", "and in lisbon", [call("get_weather", place="Lisbon")], active="ACTIVE: get_weather | place=Oslo | 2 min ago")
add("followup", "and in pounds", [call("convert_currency", amount=100, **{"from": "USD", "to": "GBP"})],
    active="ACTIVE: convert_currency | amount=100 from=USD to=EUR | 1 min ago")
add("followup", "and the day after", [call("calendar_agenda", date_offset=2)], active="ACTIVE: calendar_agenda | date_offset=1 | 1 min ago")
add("followup", "how old is it", [call("web_search", query="how old is the Eiffel Tower", topic="general")],
    active="ACTIVE: web_search | query=height of the Eiffel Tower topic=general | just now",
    history=[{"user": "how tall is the eiffel tower", "veda": "The Eiffel Tower is about 330 metres tall. Source: britannica."}])
add("followup", "where was he born", [call("web_search", query="where was Elon Musk born", topic="general")],
    active="ACTIVE: web_search | query=CEO of Tesla topic=general | just now",
    history=[{"user": "who runs tesla", "veda": "Elon Musk is the CEO of Tesla. Source: wikipedia."}])
add("followup", "ok send it", [call("gmail_send")], active="ACTIVE: gmail_draft | to=karan@example.com subject=Report ready | just now")

# ---- nothing to follow, or a target the user never named: ask, never guess ----------------------------------------------------------------------
add("no-state", "and how about sunday", clar="What would you like to know about Sunday?")
add("no-state", "what about tomorrow", clar="What would you like to know about tomorrow?")
add("no-state", "the second one", clar="Which one do you mean?")
add("destructive", "delete it", clar="Which one do you mean?")
add("destructive", "get rid of that one", clar="Which one do you mean?")
add("destructive", "remove the dentist appointment task", [call("tasks", action="delete", title="dentist appointment")])
add("destructive", "forget my favourite sweet", [call("remember", action="forget", topic="favourite sweet")])

# ---- a word is mentioned but nothing is asked --------------------------------------------------------------------------------------------------------
add("mention", "i didn't ask about the weather, what is one plus one", live=False)
add("mention", "he said it was snowing and i nodded", live=False)
add("mention", "my weather app keeps crashing, what should i do", live=False)
add("mention", "i hate being asked about the weather", live=False)
add("mention", "what does the word forecast mean", live=False)
add("mention", "my manager keeps emailing me at night and it annoys me", live=False)
add("mention", "don't add any tasks, just chat with me", live=False)
add("mention", "the word remember comes from latin", live=False)

# ---- plain conversation: knowledge, opinion, small talk (the chat model answers) --------------------------------------------------------------------------
add("chat", "explain recursion in simple words", live=False)
add("chat", "what should i name my new cat", live=False)
add("chat", "thanks that was helpful", live=False)
add("chat", "good morning veda", live=False)
add("chat", "hey veda", live=False)
add("chat", "what's seventeen times eight", live=False)
add("chat", "tell me about the history of the rupee", live=False)

# ---- about the conversation itself: only chat can answer, from its history and memory -------------------------------------------------------------------------
add("conversation", "what are we discussing", live=False)
add("conversation", "what did we talk about earlier", live=False)
add("conversation", "can you recap our conversation", live=False)

# ---- needs live data but no tool can supply it: say so, call nothing ---------------------------------------------------------------------------------------------
add("no-tool", "how bad is the traffic to the airport", live=True)
add("no-tool", "has my flight landed", live=True)
add("no-tool", "what's on my calendar next month", live=True)

# ---- asking for the one thing that is missing --------------------------------------------------------------------------------------------------------------------
add("missing", "email priya", clar="What should the email say?")
add("missing", "send an email", clar="Who should it go to and what should it say?")
add("missing", "put the dentist on my calendar", clar="What day and time should I set it for?")

# ---- the borderline behaviours, decided (labeling guide section 5) ----------------------------------------------------------------------------------------------
add("pending", "what's the capital of japan", live=False)
add("pending", "who wrote the ramayana", live=False)
add("pending", "should i carry a jacket in manali", [call("get_weather_forecast", place="Manali", date_offset=0)])
add("pending", "what time is it in tokyo", live=True)
add("pending", "tell me a joke", live=False)
add("pending", "cancel my five pm meeting", live=True)
add("pending", "move my meeting to six", live=True)
add("pending", "and in delhi", clar="What would you like to know about Delhi?")
add("pending", "what's happening", clar="What would you like to know?")

if __name__ == "__main__":
    dump(samples, OUT)
    print(f"{len(samples)} pilot samples -> {OUT.relative_to(ROOT)}")
