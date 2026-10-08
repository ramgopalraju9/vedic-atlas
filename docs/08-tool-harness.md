# 08 — Tool harness (tasks, weather, forecast, currency, web search, remember, device control)

How Veda calls tools reliably on a small quantized local model (Qwen3-4B Q4, CPU, n_ctx 4096).

## Principles
1. **One decision per turn, made by the model with the whole conversation in view.** No regex reads the user's message to pick, force or skip a tool; a regex may only constrain or veto the model's *output*.
2. **The model does the minimum**: pick tools, fill 1-3 flat arguments. Code does the rest (dates, ranges, place defaults, spoken sentences).
3. **Output is constrained**: llama.cpp grammar from a JSON schema — no malformed JSON, no unknown tool/argument.
4. **Facts come only from tool results**; replies for template tools are the tool's own sentence and never pass through the model.
5. **Fail closed**: unparseable decision -> "didn't catch that"; live data wanted but no tool -> fixed refusal; an unnamed destructive target -> ask.
6. **One source of truth per tool**: `config/tools/<name>.yaml`.

## Turn flow
```
user text -> Supervisor (bookkeeping) -> AssistantOrchestrator
  1. session id resolved once; session state -> `ACTIVE: get_weather | place=Tokyo | 3 min ago`
  2. ONE control decode: persona_lite + every tool + rules (static, cached) / ACTIVE + RECENT + TODAY + USER
        -> {"needs_live_data": bool, "calls": [<=3]} + optional "clarification": "..." (only when asking)
  3. dispatch_policy:  TOOLS | CLARIFY | CHAT | REFUSE | FAIL_CLOSED
  4. TOOLS   each call through SkillRunner (permissions / rate limit / validators / audit hooks), logged `[tool-call]`
             reply: template tools -> their own `spoken` sentence, joined in call order (0 more decodes);
                    results needing phrasing -> ONE content decode (`narrate`) + grounding check, else the plain text
     CHAT    ResponderAgent (persona, saved facts, summaries, history of this session); a reply that claims an action is refused
     others  a fixed sentence
  5. turn persisted, session state written (successful, non-destructive calls only), TurnTrace recorded
```
Decodes per turn: tool turn with template reply 1; with narration 2; chat 2; clarification/refusal 1.

## Where things live
| Piece | Location |
|---|---|
| Tool manifests (name, params, examples, `prompt_example`, reply_mode, `returns`, `max_result_tokens`, `destructive`/`destructive_when`/`target_params`, claims, cache TTL, hosts) | `config/tools/*.yaml` -> `schemas/tool_manifest_schema.py` -> `domain/entities/tool_manifest.py` |
| Prompts | `config/prompts/{persona_lite,control_stage,narrate,content_*}.md` via `tpa/filestore/file_prompt_store.py` |
| Prompt assembly + token budgets | `service/prompting/prompt_composer.py`, `domain/policies/token_budget_policy.py`, `config/prompting.yaml` |
| Control-decision schema | `domain/policies/tool_call_schema.py` (`build_control_schema`) |
| Decision, dispatch, replies | `service/agent/{control_decoder,assistant_orchestrator,tool_execution,tool_use_guard}.py`, `domain/policies/{dispatch,reply,destructive,session_state,claim_guard}_policy.py` |
| Session state | `service/session/session_state.py`, `session_contexts` table (`tpa/persistence/repositories/session_context_repository.py`), TTL `agents.session_ttl_sec` |
| Grounding | `domain/policies/grounding_policy.py` |
| Skills | `service/skills/manifest_skill.py` + `builtin/{tasks,weather,currency,web_search,remember,system_control}.py` |
| Online lookups | `service/lookup/{lookup_service,place_resolver,weather_lookup,currency_lookup,search_lookup,ttl_cache}.py` |
| Providers | `tpa/online/providers/{weather,forecast,geocode,fx,tavily}.py` via `tpa/online/http_client.py` (allow-list, no redirects, `[http]` log) |
| Traces | `domain/entities/turn_trace.py`, `tpa/persistence/repositories/trace_repository.py`, `GET /api/trace`, `veda trace` |
| Health | `service/lookup/health_service.py`, `GET /api/lookup/health`, `veda doctor` |

## Tools today
`get_weather`, `get_weather_forecast(place, date_offset 0..6)` (code validates the range and builds the sentence naming the
place and the day), `convert_currency`, `web_search` (`returns: document`, narrated), `tasks`, `remember`, and the device
tools `app_control` (open/close/focus), `volume_control` (get/set/mute/unmute), `device_status` (battery/processes),
and `current_time` (time or date, built from the device clock: before it existed "what is the time" was answered with CPU usage).
`terminal` and `file_ops` are skills but are deliberately **not** model-callable.

### Gmail and Calendar (`gmail_search`, `gmail_read`, `gmail_draft`, `gmail_send`, `calendar_agenda`, `calendar_create`)
Ports `MailPort` / `CalendarPort`; adapters `tpa/online/google/{google_auth,gmail_client,gmail_parser,calendar_client}.py`; skills
`service/skills/builtin/{gmail,calendar_agenda,calendar_create}.py`; pure rules `domain/policies/{mail,calendar}_policy.py`.
- **Linking, `veda login`:** Veda checks the sign-in when it starts (`GET /api/google/status`; the server also logs one line) and the REPL offers to fix it on the spot
  ("Sign in to Google now? [Y/n]"): not signed in, expired/revoked, or missing a permission (e.g. an old read-only calendar token). `veda login` does the same any time
  (`--check` verifies, `--manual` for a headless Raspberry Pi, `--browser` to force the local browser); the running server reloads the new token with no restart
  (`POST /api/google/reload`). Headless/manual flow: open the printed address on any device, allow access, copy the whole address of the page that then fails to load
  (127.0.0.1) and paste it back; Google's "enter a code" device flow cannot be used because it does not allow Gmail/Calendar scopes. Skip the prompt with `VEDA_SKIP_GOOGLE_PROMPT=1`.
  Manual details: `GOOGLE_API_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` in `.env`, then `python scripts/google_auth.py` once (browser consent, PKCE, loopback) writes
  `GOOGLE_REFRESH_TOKEN` to `.env`; `--check` verifies it. Scopes: gmail.readonly, gmail.compose, calendar.events — Veda reads and adds events (no attendees, so nobody is invited) and never deletes mail or events. After upgrading from calendar.readonly, run `python scripts/google_auth.py` again to grant the new permission.
  Until linked the tools answer "I can't check your email yet because it isn't set up". A consent screen left in *Testing* expires the token after 7 days.
- **Two planes:** `gmail_search` speaks sender + subject only (template); `folder: sent` makes the CODE search `in:sent` and speak the recipient ("To Manoj Routhu: ..."), so a wrong model-written query cannot look in the inbox for mail the user sent. `gmail_read` is `returns: document`: one body (<=2000 chars, quotes stripped), summarised by the
  content decode, never in a control prompt; its spoken fallback never carries the body.
- **Sending is two turns by construction:** `gmail_draft` resolves a name to an address from the user's own mail (only when exactly ONE address matches, else it asks),
  saves a Gmail draft and reads it back; `gmail_send` takes **no arguments** and can send only that draft (`service/mail/draft_outbox.py`: in memory, 10 min, this session,
  consumed on the attempt so a repeat or a retry cannot send twice). It is `destructive: true` (no state inheritance, never in a multi-call). There is no code path that
  sends text composed in the same turn. `permission_level` is `notify`, not `approve`: `PermissionManager`'s approval wait has no resolver wired (nothing calls its
  `approve()`), so an `approve` tool would always time out; the user's separate "send it" after hearing the draft is the confirmation.
- **Privacy:** manifests set `private: true`: results are kept out of the trace, the `[tool-call]` log, the audit log and the API's `skill_calls`. Mail/calendar text is
  untrusted input; `clean_text` flattens it before it is spoken (and therefore saved into history).
- **Calendar:** primary calendar. `calendar_agenda` reads: the model picks `date_offset` 0..6 and `days` 1..7, code does the dates. `calendar_create(title, date_offset 0..6, time "HH:MM", duration_minutes?)` adds one event in one turn: it sets NO attendees (nobody is invited), refuses a time already past, does not add an identical event twice, and reads the confirmation back from the event Google stored. A sign-in without `calendar.events` answers "I don't have permission to add calendar events yet". Tasks and the calendar are separate: the `tasks` tool refuses a title that describes a calendar entry (`calendar_policy.looks_like_calendar_entry`) and says so, instead of filing "block calendar for Manoj" as a to-do.
- **Search follow-ups:** `web_search` refuses a query that is only a pronoun ("where is it", "what is happening there": `query_policy.has_unresolved_reference`) and asks what is meant, because the control model is supposed to rewrite a follow-up with the subject named from ACTIVE/RECENT. Spoken search answers split sentences after initials ("N. R. Murthy") and titles ("Dr.") correctly.

## The remember tool
`config/tools/remember.yaml` keeps lasting facts about the user: preferences, allergies, names, routines. Actions: `save` (topic + value), `forget` (topic), `list`.

- **Stored as** one line per fact, `favourite sweet: gulab jamun`, in `data/knowledge.json` (the `KnowledgeBase`; `domain/policies/fact_policy.py` defines the shape). The topic is the key, so a new value *replaces* the old one and the reply says what it replaced.
- **Always in the chat prompt.** `ResponderAgent.build_prompt` includes every saved fact on every turn, so "what's my favourite sweet?" is answered from the prompt and needs no tool (a `list` call is harmless but unnecessary).
- `forget` is destructive: the topic must be named in the user's own words, or the turn asks which one. `claims` stops a reply such as "I'll remember that" when nothing was saved.
- **Limits.** Max 40 facts (`MAX_FACTS`), topic <= 60 and value <= 200 characters. Passwords, PINs, card and account numbers are refused. Nothing is saved unless the user said it.

## Adding a tool
1. Write `config/tools/<name>.yaml` (description <= 25 words, <= 6 examples, at most one `prompt_example: true`; set `destructive_when` + `target_params` for anything irreversible).
2. Write a `ManifestSkill` subclass (`run()` only), register it in `server.py`.
3. Add cases to `tests/eval/orchestrator_golden.yaml`; never tune against `orchestrator_heldout.yaml`. Measure with `python scripts/spike_decision_protocol.py --variants A5`.
4. The static control prompt grows with every tool (~100 tokens each with its example): check `PromptBudgets.control` still leaves >= 400 tokens (a test enforces it).

## Online tools
- Weather / forecast: Open-Meteo (place -> coordinates via its geocoder; unknown place -> web-search coordinates, validated; current-weather provider down -> labelled-approximate web-search answer).
- Currency: Frankfurter (ECB, ~30 currencies) with open.er-api.com fallback (166 currencies; attribution: https://www.exchangerate-api.com).
- Search: Tavily. Key in `.env` as `TAVILY_API_KEY` (git-ignored). News questions speak the top dated headlines; general questions speak the provider's answer.
- `privacy.online.enabled: false` removes all online tools.

## Operating it
- `veda doctor` — live probe of every provider (geocoding, weather, currency, web_search incl. key presence).
- `veda trace [n]` / `/trace` — what each turn really did (decided, calls, `needs_live_data`, `clarified`, `state_used`, timings, prompt tokens).
- `python scripts/spike_decision_protocol.py --variants A5 [--golden tests/eval/orchestrator_heldout.yaml]` — decision accuracy through the real pipeline; `--rejudge <saved.json> [--phase4]` re-scores saved model output offline.
- `python scripts/measure_prompt_budget.py [--speed]` — exact token counts per prompt file.
- Log tags: `[control] [tool-call] [tool-guard] [lookup] [http] [place] [fx] [health] [session-state]`.

## Known limits (measured; see docs/10-orchestrator-review-and-plan.md §6c-§6g)
- **Decision accuracy on a 4B Q4 model**: 84% on the tuned golden set, ~62% on the held-out set. Typical misses: general knowledge sent to `web_search`, a private-data question ("my meetings") mapped onto `tasks`, a follow-up with no context guessing a topic, and a two-intent message dropping the second call.
- **Latency is decode-bound** (~0.35 s per output token on the dev laptop): ~8 s for a chat turn's decision, ~13 s for a tool call. A Raspberry Pi is slower; the small profile (`n_ctx: 2048`) cannot hold the ~2,300-token control prompt.
- "I need to ..." creates a task by design, so "I need to open chrome" may be read as a task.
- `LlamaCppClient` holds a thread lock around every decode: a timed-out call's thread keeps running, and a second decode on the same llama.cpp context would crash the server. The reply after a timed-out call waits for it to finish.
- `SkillRunner` permissions are per skill, not per argument, so `app_control close` cannot require a higher level than `open`.
