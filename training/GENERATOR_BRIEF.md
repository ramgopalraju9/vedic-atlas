# Brief for whoever writes training samples for the router

You are writing labelled examples for **Veda**, a voice-first personal assistant. A small model (the "router") reads what the user said and
decides, in one JSON object, whether to call a tool, ask a question, say it cannot look something up, or hand the turn to chat. Your examples
teach it. **Quality of the labels matters more than quantity.** Read, in this order:

1. `training/labeling_guide.md` — the rules. Every borderline behaviour is decided (section 5). Follow it exactly.
2. `training/make_pilot.py` — about 80 correct examples in the exact format, one per idea.
3. `config/tools/*.yaml` — the 15 tools, their parameters and ranges (`name`, `params`, `enum`, `description`).
4. `training/sample.py` — the JSON fields.

**Do not open `tests/eval/` (the evaluation sets) or `training/data/` (private).** Do not copy sentences or argument values from
`config/prompts/control_stage.md` or the `examples:` in the tool files. The checker blocks copies of evaluation cases anyway.
(The agent that paraphrases the real seeds is the only one allowed to read `training/data/seeds.jsonl`.)

## One sample = one JSON line

```json
{"id":"g03-0001","category":"explicit","user":"add buy filter coffee to my tasks","label":{"needs_live_data":true,"calls":[{"tool":"tasks","args":{"action":"add","title":"buy filter coffee"}}]},"active":"","history":[],"today":"2026-10-14T09:20","tools":null,"source":"synthetic","group":"g03-tasks-add-coffee","status":"final","notes":""}
```

* `id`: your prefix + running number, unique (`g03-0001`, `g03-0002`, …).
* `category`: one of `explicit stt multi followup no-state destructive mention chat conversation no-tool missing refuse-twin clarify-twin seed`.
* `user`: what the speech-to-text engine hands over (see Style).
* `label`: `{"needs_live_data": bool, "calls": [{"tool": "...", "args": {...}}], "clarification": "one short question"}` — leave `clarification` out unless asking. Calls ⇒ flag true. A clarification ⇒ no calls and flag false. Refuse = flag true, no calls. Chat = flag false, no calls.
* `active`: `""` when nothing is active, otherwise exactly `ACTIVE: <tool> | k=v k=v | just now` or `... | N min ago` (N 1–14), e.g. `ACTIVE: get_weather | place=Oslo | 2 min ago`, `ACTIVE: gmail_draft | to=asha@example.com subject=Running late | just now`. The slots are the arguments of that earlier call.
* `history`: up to two earlier exchanges `[{"user": "...", "veda": "..."}]`; `veda` is what Veda said aloud: one or two short spoken sentences ("In Tokyo it's 18 degrees and cloudy.", "You have 2 events tomorrow: Standup at 10 AM, Dentist at 3 PM."). Empty list when none.
* `today`: `YYYY-MM-DDTHH:MM`, vary it (October–December 2026, early morning to late night). Anything created for the future (calendar, `due_at`) must lie after `today`; `date_offset` counts days from it.
* `tools`: always `null`. `source`: `"synthetic"`. `status`: `"final"` — use `"provisional"` with a note only if, after reading the guide, you honestly cannot say what the right label is.
* `group`: all paraphrases of one idea share a group id (3–6 phrasings per idea). Different ideas get different groups.

## Style (this is speech, not typing)
* Mostly lower case, little or no punctuation, number words ("six pm", "fifty"), occasional homophones ("whether" for "weather"), dropped or repeated words, fillers ("um", "so", "uh", "okay so"), sometimes a wake phrase first ("hey veda …"). **At least a quarter of your sentences should look like this.** No keyboard typos, no quotation marks, no emoji.
* Vary length (2 to 25 words), register (blunt, polite, rambling), and word order. Do not make 40 sentences from one template with one slot swapped.
* Mix Indian and international places (Hyderabad, Pune, Kochi, Lagos, Oslo, Lima …). People are made up (Asha, Ravi, Meera, Karan, Divya, Sameer, Leela …), emails are `name@example.com`. No real persons, companies' private data, phone numbers or addresses.

## Labels — the mistakes to avoid
* A value in `args` comes from what the user said (or what ACTIVE/history already named). Never invent a time, a recipient, an email body, a task title.
* `calendar_create`: `time` only if the user said one; otherwise clarify. "Remind me at 6 pm to …" is `tasks add` with `due_at`.
* A follow-up search names its subject; it never says "he/it/there".
* Stale or irrelevant ACTIVE must not be inherited: a greeting, a new topic, or a different tool means ignore it.
* Delete/forget/complete must name the target in the user's own words; "delete it" with nothing to refer to is a clarification.
* For every refuse or clarify idea, also write a minimal supported "twin" (same words, one detail changed, that IS answerable).

## Working loop
1. Write your samples in parts of ~40–60 lines (one `Write` per part, e.g. `training/datasets/gen/part_g03_1.jsonl`).
2. After each part run, from the repo root:
   `vedic-atlas-env\Scripts\python.exe -m training.check training/datasets/gen/part_g03_1.jsonl`
   Fix every `ERROR` (they are real: wrong argument, a label the serving policy would veto, a copy of a test case, a repeated sentence). Warnings are advice.
3. When all parts are clean, join them into your final file (`training/datasets/gen/g03_<name>.jsonl`) with a shell `cat`, check it once more (must print `OK`), and delete the part files.
4. Your final answer: the file path, the exact number of samples, the category/kind counts the checker printed, and a short list of anything you were unsure about (sample ids + why). Do not paste samples into your answer.

Only create files under `training/datasets/gen/`. Do not edit any other file.
