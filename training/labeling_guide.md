# Labeling guide for the router

The router is the one model call that decides each turn: use a tool, ask a question, chat, or say it cannot look something up.
This guide is the single source of truth for what the right decision is. The teacher (Claude) labels from it, a second labeler
checks a tenth of the samples against it, and the evaluation sets are judged by it. **Change behaviour here first, then
regenerate and retrain.** Every borderline behaviour has now been decided (section 5); a sample that is still `provisional` would mean a new
open question, and stays out of a real build until it is decided.

## 1. The decision

```
{"needs_live_data": true|false, "calls": [{"tool": "...", "args": {...}}], "clarification": "one short question"}   (clarification only when asking)
```

| The user… | calls | needs_live_data | clarification | Result at serving |
|---|---|---|---|---|
| asks for what a tool does, or states something it records | 1–3 calls | true | – | the tool runs |
| needs information no tool can supply (traffic, flight status, a day beyond the tool's range) | empty | true | – | "I can't look that up" |
| cannot be served without a missing piece (nothing to follow, no target, no content) | empty | false | one short question | the question is asked |
| chats, asks knowledge, mentions a topic, negates, greets, asks about the conversation itself | empty | false | – | the chat model answers |

Rules the validator enforces: calls ⇒ `needs_live_data` true; a clarification ⇒ no calls and `needs_live_data` false; at most 3 calls; every
argument exists, has the right type, and respects the tool's enum and ranges; `gmail_send` only right after a draft was read back (ACTIVE shows
`gmail_draft`); a delete/forget/complete names its target in the user's own words; destructive calls never join a multi-call.

## 2. Decide in this order

1. **Greeting, thanks, small talk, a question about the conversation itself** ("what are we discussing", "what did we talk about earlier") → chat (no call, flag false).
2. **Mention without a request** (quoting someone, complaining about a topic, negating "I didn't ask about…", asking what a word means, talking about an app that shows weather) → chat. Naming a topic is not asking for the tool.
3. **Explicit request or something a tool records** → the tool.
4. **Needs live data, no tool can give it** → refuse (flag true, no calls). Never route it to a tool that does something else (the to-do list is not a calendar or an inbox).
5. **A missing piece the user must supply** → clarify with ONE short question. Never invent a place, task, time, recipient or topic.
6. **Follow-ups**: use ACTIVE/RECENT only when the message continues it; a value the user names replaces the inherited one. When ACTIVE is none and RECENT is none, a message that only continues something ("and sunday?") has nothing to follow → clarify.
7. **General knowledge that does not change** → chat. **Facts that change** (a current office-holder, scores, prices, news) → `web_search`. (Decision 1 in section 5: stable trivia is chat.)

## 3. Arguments

* **Take values from what the user said**, or from ACTIVE/RECENT when continuing. Never copy a value from a prompt example (a title like "Software review", an email body "Hi Priya, I'll be ten minutes late.").
* **Self-contained queries.** A `web_search` query for a follow-up names the subject ("where was Elon Musk born"), never "he/it/they/there".
* **Times and dates.** `date_offset` is days from today (0 today … 6). `time` is 24-hour `HH:MM` and is included **only if the user said a time**; if they did not, clarify instead ("What time should I set it for?"). Durations 5–480 minutes. "Remind me at 6 pm to …" is a **task with `due_at`** (the reminder engine speaks tasks with a due time); "put X on my calendar" is `calendar_create`.
* **Email.** `gmail_draft` needs a recipient AND something to say; "email Priya" alone → clarify "What should the email say?". The body is written from what the user said, nothing added. `gmail_send` takes no arguments.
* **Search.** `topic: news` for headlines, scores and anything "latest"; otherwise `general`. Mail searches use Gmail operators (`from:`, `to:`, `newer_than:1d`) and `folder: sent` for mail the user sent.
* **Currency** codes are ISO uppercase (`INR`, `USD`). **Places** keep the user's spelling, capitalised.
* **Tasks.** `title` is a few words naming the task, in the user's words; `complete`/`delete` must name it (no pronouns, no "the first one").

## 4. Tools at a glance
`get_weather(place?)` current weather · `get_weather_forecast(place?, date_offset)` 0–6 days, also "umbrella/coat" questions · `convert_currency(amount, from, to)` ·
`web_search(query, topic?)` · `tasks(action add|list|complete|delete, title?, due_at?, include_done?)` · `remember(action save|forget|list, topic?, value?)` ·
`app_control(open|close|focus, name)` · `volume_control(get|set|mute|unmute, level?)` · `device_status(battery|processes)` ·
`gmail_search(query?, unread_only?, folder?)` · `gmail_read(query?)` · `gmail_draft(to, subject, body)` · `gmail_send()` ·
`calendar_agenda(date_offset?, days?)` · `calendar_create(title, date_offset?, time?, duration_minutes?)` · `current_time(what?)`.
There is **no** tool to move, cancel or delete a calendar event, to read the time in another city, to give traffic or flight status.

## 5. Decisions on the borderline behaviours (all accepted by the product owner on 2026-10-09; none is open)

| # | Situation | Decision | Why it matters |
|---|---|---|---|
| 1 | Stable trivia ("capital of Japan", "who wrote the Ramayana", "how far is the moon") | chat, no tool | the old golden set said "no tool" but a 4B model searches the web |
| 2 | "Should I bring an umbrella / carry a jacket" | `get_weather_forecast` (date_offset 0 unless a day is named) | it was labelled `get_weather` in one place and forecast in another |
| 3 | Live things with no tool (traffic, flight status) | refuse | – |
| 4 | "What time is it in Tokyo" | refuse (flag true, no calls) | the clock tool only knows this device's time |
| 5 | Cancel / move / delete a calendar event | refuse | there is no tool; the model otherwise creates a new event |
| 6 | "Tell me a joke" | chat, never a clarifying question | a 4B model asks "what kind?" |
| 7 | "And in Delhi?" with nothing active | clarify "What would you like to know about Delhi?" | it names a place but not the topic |
| 7b | Any "and <place/day/thing>" with nothing active | the same clarification, naming the thing | consistent wording |
| 8 | A bare "what's happening?" / "what's going on?" | clarify "What would you like to know?" | could mean the news, your day, or the conversation |

## 6. Speech, not typing
The input is speech-to-text output: lower case, no or little punctuation, number words ("six pm", "forty"), homophones ("whether" for "weather"),
dropped or doubled words, fillers ("um", "uh", "so"), and often a wake phrase in front ("hey veda …"). At least a quarter of the utterances should look like this.
Do not add keyboard typos or quotation marks: nobody types to Veda.

## 7. What the whole set should look like (4,000 samples)
about 45% calls (spread over all 15 tools, weighted by how often they are used), 30% chat (knowledge, greetings, mentions, negations, conversation questions),
8% clarify, 7% refuse; about 50% of samples carry ACTIVE/RECENT, and half of those carry stale or irrelevant state that must NOT be inherited; about 4%
multi-call; every refuse and clarify sample has a minimal "twin" that is supported ("cancel my 5 pm meeting" next to "what's at 5 pm"); every tool appears
in at least 60 samples; each idea is phrased 3–6 ways, all in one `group`.

## 8. Hygiene
* Synthetic people, places, addresses only (`asha@example.com`). Never a real name or address from a conversation.
* No sample equals or nearly equals an evaluation case (the build drops them) and no prompt-example text is reused as an argument (the validator rejects it).
* One decision per sample. If two experts could disagree about the label, it is **provisional**, not guessed.
* A second labeler (different prompt, ideally a different model) re-labels 10%; every disagreement is read by a person and either the sample or this guide is fixed.
