# Veda / Vedic Atlas — Architecture Overview

Veda is an on-device voice/text assistant. The defining constraint shaping every
layer of this codebase is **REQ-M-02/M-03: all reasoning happens locally** — no
cloud LLM, no silent model downloads, no network call that wasn't explicitly
allow-listed. That single constraint explains why the architecture looks the
way it does: strict hexagonal boundaries exist specifically so that "local
Ollama today, local llama.cpp tomorrow, a Raspberry Pi with GPIO hardware
next year" are all swap-in adapters, never rewrites.

This document is the map. Read it first, then follow the links into whichever
layer you need to work in.

## The layers, top to bottom

```
┌─────────────────────────────────────────────────────────────┐
│  controller/   FastAPI routes, SSE streaming, CLI client      │  HTTP / terminal boundary
├─────────────────────────────────────────────────────────────┤
│  service/      Agents, skills, governance, guardrails,        │  orchestration, policy,
│                conversation, memory, voice pipeline, hooks    │  async coordination
├─────────────────────────────────────────────────────────────┤
│  domain/       Entities, value objects, ports (Protocols),    │  pure contracts +
│                pure policy functions, events                 │  pure business rules
├─────────────────────────────────────────────────────────────┤
│  tpa/          Concrete adapters implementing domain/ports/   │  I/O, hardware, SDKs
├─────────────────────────────────────────────────────────────┤
│  core/         Config loading, constants, enums, logging      │  cross-cutting infra
│  exceptions/   AppException + global FastAPI error handlers   │
│  schemas/      Pydantic request/response models               │
└─────────────────────────────────────────────────────────────┘
```

This is a **ports-and-adapters (hexagonal) architecture**:

- **`domain/ports/`** defines ~20 `typing.Protocol` interfaces — `InferencePort`,
  `AudioCapturePort`, `STTPort`, `SystemControlPort`, `ConversationRepositoryPort`,
  etc. These are pure contracts: no implementation, no I/O, no imports of
  anything outside `domain/`.
- **`tpa/`** ("third-party adapters") provides the concrete implementations —
  `OllamaClient` implements `InferencePort`, `SoundDeviceCapture` implements
  `AudioCapturePort`, `WindowsSystemAdapter`/`LinuxSystemAdapter` implement
  `SystemControlPort`, and so on. This is the only layer allowed to import
  third-party SDKs (`sounddevice`, `psutil`, `sqlalchemy`, `httpx`, `win32gui`,
  `RPi.GPIO`, ...).
- **`service/`** never imports `tpa/` directly — not once, anywhere. Every
  service-layer class receives its dependencies as constructor-injected
  `domain.ports.*` types. This is enforced by convention/discipline, not a
  linter rule, but it holds throughout the codebase.
- **`src/server.py`** is the **composition root** — the one place where
  concrete `tpa/` adapters are constructed and wired into `domain/ports/`
  interfaces, then handed to `service/` classes. See
  [04-controller-layer.md](04-controller-layer.md#composition-root) for the
  full bootstrap sequence.

See [01-domain-layer.md](01-domain-layer.md) for the full port catalog and
[02-tpa-adapters.md](02-tpa-adapters.md) for what implements each one.

## Why this shape

The docstrings throughout the codebase repeatedly reference a **donor
codebase** called `veda` (lowercase) that this project was migrated/ported
from, plus a **migration ledger** (`docs/migration/MIGRATION_LEDGER.md`,
referenced but not covered by this doc set) recording batch-by-batch porting
decisions. Recurring porting patterns worth knowing about:

- **Vision/Teams/cloud-CLI features were dropped.** The donor had a
  Person/FaceEmbedding/Observation vision pipeline and Teams integration;
  neither exists here. Several files' docstrings explicitly note "dropped,
  out of scope" for these.
- **Cloud LLM backends are a hard-reject, not just unconfigured.**
  `tpa/inference/factory.py`'s `build_inference_client()` raises
  `UnsupportedBackendError` if asked for `"claude"`/`"copilot"`/
  `"hybrid_cloud"` — this is an enforcement point for REQ-M-02/M-03, not
  a missing feature.
- **No silent model downloads.** Every model-loading adapter (STT, TTS,
  embeddings, the GGUF inference path, wake-word) checks the model file/
  directory exists and raises a clear `FileNotFoundError` instead of
  fetching it — this was a deliberate correction from the donor's behavior
  in at least one case (`FasterWhisperProvider`'s docstring calls out that
  the donor silently downloaded on first use).
- **Two independent approval mechanisms exist in parallel**: `service/approval/approval_broker.py`'s UI-facing
  approval flow, distinct from `guardrails/permissions.py`'s skill-level approval gate. See
  [03-service-layer.md](03-service-layer.md) for both. (Agent routing no longer exists: every turn is decided by
  the one `AssistantOrchestrator` control decode.)

## Request flow, traced end to end

A `POST /api/stream` call touches almost every layer, so it's the best
single example to hold in your head:

1. **`request_context` middleware** stamps an 8-char request ID into a
   `ContextVar` (so concurrent requests' log lines don't interleave
   confusingly) — see [04-controller-layer.md](04-controller-layer.md).
2. **`controller/routes/stream.py`** builds a `domain.entities.agent_context.AgentContext`
   from the request body and pulls `SupervisorAgent` via
   `Depends(get_supervisor)` (from `controller/dependencies/providers.py`,
   reading `request.app.state.supervisor`).
3. **`SupervisorAgent.execute_stream(ctx)`** (`service/agent/supervisor.py`) hands the turn to the
   **`AssistantOrchestrator`**, which makes ONE grammar-constrained control decision (which tools, if any, with
   what arguments — seeing the recent conversation and the session state), runs the chosen tools through
   `SkillRunner`, and replies from the tool's own sentence; only a turn that needs no tool reaches the chat model.
4. For a chat turn, **`ResponderAgent.execute(ctx)`** (`service/agent/responder.py`) builds a
   cache-friendly prompt (persona block first, since it's byte-identical
   every turn and keeps the inference backend's KV-cache warm) and calls
   `InferencePort.complete(...)`.
5. That `InferencePort` is actually
   `SingleFlight(GracefulDegradation(OllamaClient))` — see
   [03-service-layer.md](03-service-layer.md#serviceinference) — composed
   in `server.py` at boot. `SingleFlight` serializes concurrent calls (CPU-
   bound local inference doesn't parallelize usefully); `GracefulDegradation`
   retries once and can fall back to a secondary backend.
6. The reply streams back chunk by chunk through `execute_stream` →
   SSE frames (`controller/sse/`) → the CLI.
7. Any unhandled exception anywhere in 2–6 is caught by the global handlers
   in `exceptions/handlers.py` and normalized into the house `AppResponse`
   envelope — see [05-exception-handling.md](05-exception-handling.md).

## Two parallel interfaces, one backend

The FastAPI server (`src/server.py`, mounted routes under `/api/*`) and the
terminal CLI (`src/controller/cli/`, the `veda` command) are **both clients
of the same running server** — the CLI is not a separate code path into the
business logic. `controller/cli/client.py`'s `VedaClient` talks to the
FastAPI app purely over HTTP/SSE (and can auto-spawn the server as a
subprocess if it isn't already running). This means the REST API is the one
true interface into the system; the CLI is UX sugar on top of it.

## Where to go next

| Doc | Covers |
|---|---|
| [01-domain-layer.md](01-domain-layer.md) | Entities, value objects, ports (full contracts), policies, events |
| [02-tpa-adapters.md](02-tpa-adapters.md) | Every concrete adapter, grouped by which port it implements |
| [03-service-layer.md](03-service-layer.md) | Agents, skills, governance vs. guardrails, inference resilience, voice pipeline, and every other `service/` subpackage |
| [04-controller-layer.md](04-controller-layer.md) | Routes, SSE, CLI, the composition root (`server.py`), full bootstrap sequence |
| [05-exception-handling.md](05-exception-handling.md) | `AppException`, error codes, the `AppResponse` envelope, HTTP status mapping |
| [06-configuration.md](06-configuration.md) | Config loading, `config/*.yaml`, constants, logging, the full `AppConfig` schema |
| [08-tool-harness.md](08-tool-harness.md) | How tool calls work on the small local model: manifests, staged turns, online tools (weather, currency, search), traces, evaluation |
