# Stage 11 — Repo hygiene, setup scripts, environment issues

*Oct 9 2026.*

## Trigger
On another machine `veda` failed with `ImportError: cannot import name 'google_login'`: that file (and ~55 others) had never been committed.

## What we did
- Commits `9f8754d` (Google, calendar/Gmail, clock, reminders, memory, tests: 56 files) and `e8ce7de` (training pipeline, datasets, clean test set, router profiles: 43 files), pushed to `feature/unified-orchestrator`. `8f4a497` (below) is local until pushed.
- Not committed on purpose: virtualenv, `.idea/`, egg-info, `wakeword_training/` (488 MB), `training/datasets/build_*/`, GGUFs, adapters, seeds (`data/` is git-ignored).
- `8f4a497`: `run.sh`/`run.bat` find the venv (`.venv`, `vedic-atlas-env`, `$VENV`); `.env.example` lists the router profiles; `.gitignore` covers the whole egg-info folder; `setup_pi.sh` summary now includes the Google sign-in (`veda login --manual`) and the manual router-GGUF copy. All seven download URLs in `setup_pi.sh` were checked (HTTP 200); the script itself was dry-run only.

## Metrics
12/12 of the pyproject/wake-feature tests pass. Profiles checked by loading each: `qwen3-0.6b/1.7b/4b` → `n_ctx` 3072, 4 history turns, 0 summaries, compact persona; memory settings identical under every profile. Your `.env` currently selects `qwen3-4b`.

## Open issue
`import sqlalchemy` fails on this laptop (Windows Application Control blocks `_processors_cy…pyd`, SQLAlchemy 2.1.3). The reminders, memory-session and exchange-index test files cannot load, and the server probably cannot start here. Renaming the blocked `.pyd` so the pure-Python module is used would probably fix it (not done). The Pi has no such policy. `docs/` is git-ignored, so these stage files are not pushed unless force-added (`git add -f docs/stages`).

## Decision
First end-to-end run on the Pi with the untuned base model; the tuned router is parked until v3 data is built.
