# Stage 3 — Reminders (no LLM), Google sign-in bootstrap, startup

*Oct 8 2026. Sources: `docs/11-reminders.md`, `docs/HANDOFF.md`.*

## Goal
1. Remind about calendar events and due tasks without the model.
2. Make sign-in and `veda` startup work on a headless Raspberry Pi.

## What we built
**Reminders** — a timer, no model call: a heads-up 15 minutes before (configurable) and one at the start; due-time tasks included; speaks only when Veda is idle and you are not talking; speaks even when the mic is muted; text is also printed in the REPL. Pure `reminder_policy`, scheduler/announcer/ledger (`reminder_fired` table so nothing repeats), `config/reminders.yaml`.
**Sign-in** — `veda login` (browser flow), `veda login --manual` for a headless Pi (open the address on another device, paste the result back), `veda login --check`. The token is saved, so a restart needs no new sign-in. Veda start-up now checks the sign-in instead of failing at the first calendar request.
**Startup** — `veda` reuses a running server or starts one; waits for it; a bad `VEDA_START_TIMEOUT` no longer crashes; the 30-second startup timeout / restart loop was fixed.

## Metrics
No accuracy metric (the part is model-free). Verified by unit tests (`test_reminders.py`, `test_google_login.py`, `test_cli_restart.py`) and manual runs. Part of the ~780-test suite.

## Where it failed (found by edge-case testing, then fixed)
Double announcement race; completed tasks still reminded; 12-hour time parsing; reminder text rendering in the REPL (markdown ate a bare "2.").

## Decision / why we moved on
Everything time-based stays model-free (reliable, costs no latency). The remaining weakness was not here but in routing and memory, so work moved to Stage 4–5.
