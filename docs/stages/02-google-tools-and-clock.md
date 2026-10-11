# Stage 2 — Gmail, Calendar and clock tools; log-driven fixes

*Oct 8 2026. Sources: `docs/HANDOFF.md`, `docs/08-tool-harness.md`.*

## Goal
Add real tools beyond weather/currency/search/tasks: `gmail_search`, `gmail_read`, `gmail_draft`, `gmail_send`, `calendar_agenda`, `calendar_create`, `current_time` (16 tools in all). Tasks go to `tasks`, calendar data to the calendar tools, nothing else changes.

## What we built
- Skills, manifests (`config/tools/*.yaml`), Google OAuth client and token handling, mail/calendar policies, `exchange`-style spoken replies.
- **Write guards in code** (the model still invents values): `calendar_create` trusts a time only if the user's own words contain one (`calendar_policy.message_states_time`); `gmail_send` only right after a draft was read back; sent-mail search; draft outbox.
- Fixes from a pasted CLI log: calendar write, clock answers, sent-mail search, search follow-ups ("what about X" keeps the topic).
- Prompt trimmed (duplicate examples dropped) to fit two more tools: static prompt now ~2,597 of the 2,600 tokens the tests allow.

## Metrics (A1, temp 0.1, 4B)
| Measurement | Before | After |
|---|---|---|
| Golden set | 45/57 | 46/57 |
| 13 new cases (calendar writes, clock, sent mail, search follow-ups) | — | 12/13 |
| Held-out (A5) | 28/40 (original 40 cases) | 36/46; the 6 new cases all pass, the original 40 score 30/40 |
| Full test suite | — | ~780 passed |

## Where it failed
- "Should I bring an umbrella?" flipped to `get_weather` instead of `get_weather_forecast`.
- The model still invents a time when none is spoken (handled by the guard, not the model).
- No room left in the prompt for another tool without removing text.

## Decision / why we moved on
Keep guards for anything that writes or sends. Treat the 2,600-token prompt ceiling as a hard budget (it is also the main latency cost on the Pi). The recurring prompt crowding is one argument for training the rules *into* the model (Stage 6+).
