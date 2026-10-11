# Plan: Trim the Veda API down to what the CLI uses

Status: **proposal, no code written.** 2026-10-07.

**Decision:** Veda stays server-based (the server owns the voice loop, mic, model and DB).
We only remove API surface nothing uses. No change to the architecture, no new process model.

Earlier serverless/control-socket ideas are dropped.

---

## 1. Scope

Delete the REST endpoints that no CLI command calls. The voice assistant is unaffected:
it runs inside the server and calls the supervisor directly, never its own routes.

## 2. Route disposition

### Keep (the CLI uses these)

| Route file | Endpoints kept |
|---|---|
| health | `GET /health` |
| stream | `POST /stream` |
| persona | `GET, POST /persona` |
| approval | `GET /approval/pending`, `POST /approval/{id}/approve`, `POST /approval/{id}/deny`, `GET /approval/yolo`, `POST /approval/yolo/on`, `POST /approval/yolo/off` |
| config | `GET, POST /config/proactivity` |
| privacy | `GET /privacy/status`, `POST /privacy/mute`, `POST /privacy/mute/toggle` |
| voice | `GET /voice/status` |
| trace | `GET /trace` |
| lookup | `GET /lookup/health` |
| tasks | `GET /tasks`, `POST /tasks`, `POST /tasks/{id}/complete` |
| governance | `GET /governance/status`, `GET /governance/audit/stats` |
| admin | `POST /admin/shutdown` |
| speakers | all 6 endpoints (kept: enrolment is wanted for a future speaker-recognition mode instead of a wake word) |
| knowledge | `GET, POST /knowledge`, `DELETE /knowledge/{index}` (kept so stored personal facts can be reviewed and deleted) |

### Delete

| Route file | What goes |
|---|---|
| `chat.py` | whole file (`POST /chat`; the CLI uses `/stream`) |
| `memory.py` | whole file (debug views) |
| `ambient.py` | whole file (publish/stream; nothing consumes them) |
| `system.py` | whole file (Windows active-window probe) |
| `lookup.py` | `GET /lookup/providers`, `POST /lookup/fetch` |
| `governance.py` | `/audit/entries`, `/audit/verify`, `/health`, `/policies` |
| `voice.py` | `/voice/config`, `/voice/start`, `/voice/stop`, `/voice/say` |
| `approval.py` | `POST /approval/request` |
| `tasks.py` | `DELETE /tasks/{id}` |

Net: 4 route files removed, 5 trimmed, 11 kept as is.

## 3. Knock-on changes

| Item | Change |
|---|---|
| `server.py` | drop imports and `include_router` for chat, memory, ambient, system |
| `controller/dependencies/providers.py` | remove accessors that lose their last caller: `get_memory`, `get_semantic_recall`, `get_lookup_service`, `get_event_bus` (verify each with grep first) |
| `schemas/` | delete `ChatRequest`/`ChatResponse` (keep `StreamRequest`), `system.py`; keep `speakers.py`; update `schemas/__init__.py` re-exports |
| Trimmed route files | remove the now-unused request models and imports (`FactQueryBody`, `SayBody`, `VoiceBrowserConfig`, `ApprovalRequestBody`, `_session`) |
| `scripts/demo.py` | **delete the file** (decided; it only drives the REST API for live demos) |
| `docs/04-controller-layer.md`, `docs/00-overview.md` | update the routes table and the endpoint walk-through |
| Launch scripts | `run.sh` and README point to a root `server.py` that does not exist; `run.bat` uses `.venv` instead of `vedic-atlas-env`. Fix to call `veda` / `veda server` |

Not touched: services, adapters, `EventBus`, `ApprovalBroker`, and all speaker code (`service/speakers/`, `tpa/speaker/*`, enrolment wiring in `server.py`).

## 4. Checks before deleting

1. **Tests:** only `test_routing_modes.py` (health) and `test_traces_and_grounding.py` (trace) touch routes, and both use kept routes. Grep for any others before starting.
2. **Provider accessors:** grep each accessor listed above for remaining callers before removal.
3. **Outside callers:** you confirmed none beyond the CLI.

## 5. Steps

1. Run `pytest -q` and note the baseline.
2. Delete the 5 route files, update `server.py` and `providers.py`.
3. Trim the 5 route files and remove their unused models and schemas.
4. Delete `scripts/demo.py`, fix launch scripts, update docs.
5. `pytest -q` again and compare with the baseline.

## 6. Verification

- `pytest -q` matches the baseline.
- `veda server` boots; the `/docs` page lists exactly the kept endpoints.
- REPL smoke test: chat streams, `/persona`, `/task`, `/trace`, `/doctor`, `/mute`, `/unmute`, `/status`, `/approve` and `/yolo` all work.
- Voice loop starts at boot and answers a "Hey Veda" turn; `/voice/status` reports it running.
- `grep -rn "chat\.\|memory_route\|ambient\|speakers" src/server.py` shows no stale references.

## 7. Decisions made

- No outside REST callers.
- Stay server-based; trim the API only.
- `knowledge` route kept.
- `scripts/demo.py` deleted.
- Fix `run.sh`, `run.bat` and README launcher references in the same change.

## 8. Status

**Implemented 2026-10-07.** Baseline before: 510 passed, 6 failed. After: 510 passed, same 6 failed (`tests/test_golden_routing.py`, routing cases; failing before this change and unrelated to it). Mounted endpoints checked via the OpenAPI schema and match section 2.
