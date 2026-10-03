# Service Layer

`src/service/` is the orchestration layer: multi-agent routing, skill
execution, governance/guardrail enforcement, memory, conversation
management, and the voice pipeline. Three rules hold across every file in
this layer:

1. **Zero `tpa/` imports.** Not one file in `service/` imports anything
   from `tpa/`. Every dependency on hardware/network/DB arrives as a
   constructor-injected `domain.ports.*` type.
2. **100% constructor injection.** Concrete adapters are only ever
   constructed in `src/server.py` (the composition root) and passed in.
3. **Domain policies hold the pure decision logic; `service/` holds the
   orchestration.** Where a decision is tricky enough to want its own pure
   function (routing, permission gating, session boundaries, redaction),
   `service/` calls into `domain/policies/` rather than re-implementing the
   logic inline.

Async is the default everywhere; CPU-bound or blocking work (subprocess
calls, blocking file I/O, synchronous psutil/win32 calls) is pushed through
`asyncio.to_thread(...)`. Errors fall into two tiers: expected/recoverable
failures are caught locally, logged, and degrade to a safe default;
guardrail/policy violations raise `exceptions.exception.AppException` with
a `core.enums.ExceptionCode` — see
[05-exception-handling.md](05-exception-handling.md).

## `service/agent/` — multi-agent orchestration

```
BaseAgent (ABC)
  ├── LLMAgent                      — + InferencePort, MemoryRepositoryPort
  │     ├── ResponderAgent          — general conversation (the default agent)
  │     └── SystemAgent             — device-control planner
  ├── ToolAgent                     — owns tools declared in manifests ("tasks", "lookup")
  └── SupervisorAgent               — routes to the above (does not extend LLMAgent)
```

Every agent implements `execute(ctx: AgentContext) -> AgentResult` and
`execute_stream(ctx, cancel_event=None) -> AsyncIterator[str]`.

**`LLMAgent`** (`base_llm_agent.py`) is the shared base for text-generating
agents. It provides `build_system_prompt(ctx)`/`build_prompt(ctx)`/
`on_completion(ctx, response)` as subclass extension points,
`_memory_context(exclude_self=True)` / `_record_to_memory(...)` as
read/write helpers against `MemoryRepositoryPort`, and `_budget(ctx)` which
returns a tighter `num_predict` cap when `ctx.from_voice` is true. On a
failed/empty completion it falls back to a canned apology string rather
than propagating the error to the user.

**`AgentRegistry`** (`registry.py`) is a plain `dict[str, BaseAgent]` with
`register`/`get` (raises `AppException(AGENT_ROUTING_ERROR)` if missing)/
`list_all()`/`list_routable(exclude=None)` (used to hide the supervisor
from its own routing candidates).

### `SupervisorAgent` — the router (`supervisor.py`)

Routing order in `_pick(ctx)`:

1. 0 routable agents → `default_agent`. Exactly 1 → that one.
2. **Keyword/description match**: `RouterPolicy.pick()` wraps the pure
   `domain.policies.routing_policy.pick_agent`.
3. **LLM fallback** (only if step 2 returns `None`): one `InferencePort.complete()`
   call with a router system prompt listing every routable agent's
   name+description, expecting one line of JSON `{"agent": "...", "reason": "..."}"`.
4. **Memory fallback**: if the LLM call also fails, reuses
   `memory.last_action(agent_name="supervisor")`'s last recorded target.
5. **Final fallback**: `default_agent`.

`execute(ctx)` optionally consults `GovernanceProvider.check_action("route_to_agent", ...)`
before delegating, records the routing decision to memory, and sets
`result.delegated_to`.

`dispatch_ambient(event: AmbientEvent) -> str | None` is the separate path
for narrating non-chat events (sensor observations, reminders, system
notices): drops `HEARTBEAT` always, drops low-urgency events unless
proactivity is `chatty`, applies a `Debouncer` (per `event.dedupe_key`) and
a `RateLimiter` (bypassed for `HIGH` urgency). `set_proactivity(level)`
rebuilds the rate limiter via
`domain.policies.proactivity_policy.rate_limit_multiplier`.

### `ResponderAgent` (`responder.py`) — the default chat agent

`build_system_prompt` returns the persona prompt (`persona.py`'s
`VEDA_SYSTEM_PROMPT`) **byte-identical every turn** — a deliberate
performance choice so the inference backend's KV-cache prefix is reused.

`build_prompt(ctx)` assembles context in least-volatile-first order (for
the same cache-friendliness reason): semantic-knowledge block → system
context → recent conversation history (earlier-session summaries first, then
recent turns; "Task added…"-style exchanges filtered out) → time-of-day
context → voice-brevity instruction (if `ctx.from_voice`) → user message.
The cross-agent "recent agent activity" block is deliberately **not** included:
it held only internal routing records, which the model repeated back as if
they were the user's tasks.

The responder is plain chat only — it has no tools. Tool use lives in
`ToolAgent` (below); when a tool agent decides no tool is needed, it hands the
turn back to the responder.

`on_completion` records both sides of the turn via
`ConversationManager.add_turn()` and logs a `"chat"` memory action.

### `SystemAgent` (`system.py`) — device-control planner

Two-step design: one capped-length (`96` tokens) LLM call produces a JSON
action plan (`{"action": "open_app", "args": {...}}` — the fixed action
vocabulary is `open_app`, `close_app`, `focus_app`, `battery_info`,
`get_volume`, `set_volume`, `mute`, `top_processes`, `none`), optionally
governance-checked, then dispatched via `asyncio.to_thread` to the matching
`SystemControlPort` method. No streaming value for a single-shot planner,
so `execute_stream` just wraps `execute()`.

### `ToolAgent`, `ToolTurnRunner`, `ToolUseGuard` — staged tool use

Registered by `server.py` (when `agents.tools_enabled`) once per owner named in
`config/tools/*.yaml` (`tasks`, `lookup`). Full design: [08-tool-harness.md](08-tool-harness.md).

- **`tool_agent.py` — `ToolAgent`**: the supervisor routes to it deterministically
  from the manifests' trigger patterns (`AgentProfile.triggers`, consulted by
  `domain/policies/routing_policy.py` before keyword overlap). It runs the
  `ToolTurnRunner`; `reply is None` (no tool needed) hands the turn to the chat
  fallback, and a fallback reply that *claims* an action is replaced with "I can't
  confirm that…". Persists the turn and writes a `TurnTrace` per turn (a failing
  trace store never breaks a turn).
- **`tool_turn_runner.py` — `ToolTurnRunner`**: **decide** (one constrained
  completion → `{"calls":[...]}`; if the message matches a tool's `required_when`
  and no call came back, retry once with `minItems=1`), **execute** (each call via
  `SkillRunner.execute_skill`, so every guardrail hook still applies; each logged
  as `[tool-call]`), **reply** (the tool's own `spoken` text for template/`final`
  results, else a short narrate-stage completion that must pass the grounding
  check, else the deterministic text).
- **`tool_use_guard.py` — `ToolUseGuard`**: built from the manifests; `required_tools(msg)`
  and `claims_action(reply)`.

## "Agent" vs. "skill" — the distinction that matters

An **agent** is a named, routable, LLM-backed persona that owns a
conversational turn end-to-end and decides *what to say*. Agents live in
`AgentRegistry` and are chosen by `SupervisorAgent`.

A **skill** is a narrow, callable *capability* (terminal exec, file I/O,
task CRUD) that an agent invokes mid-turn to *do something*, always
through a uniform guardrail pipeline. Skills live in `SkillRegistry` and
are invoked via `SkillRunner` — never routed to directly.

### `service/skills/`

- **`base_skill.py` — `BaseSkill(ABC)`**: `name`, `description`,
  `permission_level`, `enabled`; abstract `execute(ctx, **params) ->
  SkillResult`. A `@skill(...)` decorator wraps a plain async function
  into a skill, auto-generating its parameter description from
  `inspect.signature`.
- **`registry.py` — `SkillRegistry`**: same shape as `AgentRegistry`, plus
  `get_prompt_descriptions(skill_names=None)`.
- **`skill_runner.py` — `SkillRunner`**: `execute_skill(ctx, skill_name,
  **params)` fires `HookEvent.PRE_SKILL` (permission/rate-limit/input
  validation) — any pre-hook returning `False` aborts with a failed
  `SkillResult` *without raising*. On success, fires
  `HookEvent.POST_SKILL` (output validation + audit), then returns.
- **`builtin/file_ops.py` — `FileOpsSkill`**: `read|write|list|exists|delete`,
  enforced against an `allowed_paths`/`blocked_paths` list (`blocked`
  always wins), raises `AppException(PATH_BLOCKED)` on violation.
- **`builtin/terminal.py` — `TerminalSkill`**: runs a shell command via
  `asyncio.create_subprocess_shell`, enforces `blocked_commands`
  (substring) and `blocked_patterns` (regex), raises
  `AppException(COMMAND_BLOCKED)` on match, truncates output at 10000
  chars, timeout-bounded.
- **`manifest_skill.py` — `ManifestSkill(BaseSkill)`**: name, description, permission and
  argument schema come from the tool's `ToolManifest`; subclasses implement only
  `run()`. Gives every tool the same envelope: unknown arguments rejected, provider /
  validation errors turned into a failed `SkillResult` with a plain `spoken` sentence
  (never a stack trace or an invented answer), and `spoken` / `final` / `source` /
  `as_of` / `cached` in `SkillResult.metadata`.
- **`builtin/tasks.py` — `TasksSkill`**: `add|list|complete|delete` over
  `service/tasks/task_service.py`. `complete`/`delete` take a `task_id` or a spoken
  phrase ("milk packets") resolved by content-word matching to exactly one open task;
  no match or an ambiguous match changes nothing and says so. Duplicate `add`s are
  rejected.
- **`builtin/weather.py`, `currency.py`, `web_search.py`**: `GetWeatherSkill`,
  `ConvertCurrencySkill`, `WebSearchSkill` — thin `ManifestSkill`s over the lookup services.

## Governance vs. guardrails — the distinction that matters

**`service/governance/`** is a pluggable, YAML-driven **policy decision
engine** for arbitrary named actions (`"route_to_agent"`,
`"system_action"`), plus per-inference-backend circuit breaking. It's
optional (`factory.py` can return `None` to disable it entirely) and is
consulted explicitly, by name, via one call:
`GovernanceProvider.check_action(action, context) -> PolicyDecision`.

**`service/guardrails/`** is **fixed, built-in enforcement** wired into the
skill-execution hook pipeline specifically: permission gating, rate
limiting, input/output content validation, and a plain JSONL audit log.
These aren't configurable rules — they're concrete classes invoked as
pre/post hooks around every `SkillRunner.execute_skill()` call.

In short: governance answers *"is this named action allowed right now, per
configurable rules?"* (agent-level); guardrails answer *"does this
specific skill call pass fixed safety checks?"* (skill-level). Both write
audit trails, but different ones — governance writes to `AuditSinkPort`
(the tamper-evident, hash-chained `SqliteAuditSink`); guardrails'
`AuditLogger` writes plain daily JSONL files. The two are documented as
able to coexist.

### `service/governance/`

- **`policy_evaluator.py` — `PolicyEvaluator`** (the one real
  `GovernanceProvider`, `name="builtin"`): loads `PolicyRule`s from the
  YAML files in `tpa/governance/policies/` at construction. Each rule has
  a `condition` (`field`/`operator`/`value` — operators: `eq`, `ne`, `in`,
  `not_in`, `contains`, `starts_with`, `matches`, `glob`), an `action`
  (`allow|deny|audit`), and a `priority` (highest first, first match
  wins). Also loads `blocked_patterns` regexes per file for
  `check_pattern(text)`.
- **`circuit_breaker.py` — `CircuitBreaker`**: per-backend-name failure
  tracking — trips after N consecutive failures, half-opens after a
  timeout.
- **`factory.py` — `build_governance(config, audit_sink) ->
  GovernanceProvider | None`**: `null` (disabled) / `builtin`
  (`PolicyEvaluator`) / `agt` (raises `UnsupportedGovernanceProviderError`
  — an external toolkit integration, not supported here). Takes an
  already-built `AuditSinkPort` rather than constructing one, so this
  module stays free of any `tpa` dependency.

### `service/guardrails/`

- **`permissions.py` — `PermissionManager`**: `check_permission(ctx,
  skill_name, permission_level, params) -> bool`. `AUTO` proceeds
  immediately; below-approval levels log and proceed; otherwise creates a
  pending approval entry and `await`s an `asyncio.Event` up to
  `approval_timeout` — raises `AppException(APPROVAL_TIMEOUT)` on timeout,
  `AppException(APPROVAL_REJECTED)` on denial. Resolved externally via
  `approve(request_id)`/`deny(request_id)`, called from the HTTP approval
  route. *(Distinct from `service/approval/approval_broker.py` — see
  below; two independently-evolved approval mechanisms both exist in this
  codebase.)*
- **`rate_limit.py` — `RateLimiter`** (guardrails version — distinct from
  `sensing/rate_limiter.py`): token-bucket, global + per-skill buckets,
  raises `AppException(RATE_LIMITED)` on exhaustion.
- **`validators.py` — `InputValidator`/`OutputValidator`**: length checks;
  `OutputValidator` additionally calls
  `domain.policies.redaction_policy.find_pii`/`find_credential`.
- **`audit_log.py` — `AuditLogger`**: one JSONL file per day
  (`AUDIT_DIR/{date}.jsonl`), four typed log methods (skill execution,
  agent event, permission event, guardrail event).

### `service/hooks/` — the wiring between guardrails and skill execution

- **`hook_types.py` — `BaseHook(ABC)`**: `name`, `event`
  (`core.enums.HookEvent`), abstract `execute(ctx, **kwargs) -> bool|None`.
- **`registry.py` — `HookRegistry`**: `fire(event, **kwargs)` — for
  `pre_*` events, a handler returning `False` short-circuits the chain and
  a raised exception propagates; for other events, exceptions are
  swallowed and logged (post-hooks warn, never abort).
- **`dispatcher.py`**: factory functions producing the actual hook
  closures registered in `server.py` — `create_permission_check_hook`,
  `create_rate_limit_hook`, `create_input_validator_hook`,
  `create_output_validator_hook`, `create_audit_hook`,
  `create_error_log_hook`.

## `service/inference/` — the resilience stack

Two composable wrapper classes, both themselves implementing
`InferencePort`, composed in `server.py` as:

```
SingleFlight( GracefulDegradation( primary_adapter, fallback_adapter ) )
```

- **`single_flight.py` — `SingleFlight`**: holds an `asyncio.Lock` for the
  *entire* call — `stream()` holds it for the whole stream duration, not
  just until the first chunk. Rationale: the target hardware runs a
  multi-GB quantized model on CPU with no GPU offload; concurrent requests
  contend for the same cores/weights rather than parallelizing, and on a
  memory-constrained Pi a second concurrent load can push into swap —
  serializing is strictly faster than thrashing. `__getattr__` forwards
  adapter-specific methods (`verify_ready`, `warmup`) transparently.
- **`graceful_degradation.py` — `GracefulDegradation`**: on failure (not a
  timeout), retries the primary once; if still failing, falls back to a
  secondary adapter if one was provided. `stream()` only falls back if
  zero chunks were yielded yet — once committed to a stream, it never
  switches mid-stream. Optional `on_success`/`on_failure` callbacks are
  where `server.py` wires the governance circuit breaker's
  `record_success`/`record_failure`.

## `service/conversation/`

- **`conversation_manager.py` — `ConversationManager`**: facade over
  `ConversationRepositoryPort`. `add_turn()` resolves `session_id`
  automatically and swallows persistence errors (a DB hiccup shouldn't
  kill the response path). `turns` returns recent un-summarized turns,
  global across sessions. `get_context_summary()` tiers older compacted
  summaries with recent raw turns.
- **`summariser.py` — `ConversationSummariser`**: background dual-loop
  (summarization tick + GC), started/stopped from `server.py`'s
  `lifespan()`. Picks the oldest eligible session (idle past a threshold,
  or turn count over a batch threshold, skipping sessions that have
  exhausted 3 retry attempts), summarizes via one LLM call, persists, and
  marks turns summarized. GC deletes old summarized turns but skips
  entirely if any session has pending failed-summary retries.

## `service/lookup/` — fact retrieval with egress enforcement

The providers answer through `LookupService`; the higher-level services below turn
their structured `FactAnswer.data` into `ToolObservation`s (`text` for logs/narration,
`spoken` for the user, `final` = ready to say as-is):

- **`place_resolver.py` — `PlaceResolver`**: name → `Place` via the geocoder (best-ranked
  match used, others logged); blank/"here" → the configured default place; unknown name →
  coordinates parsed from a web search (accepted only if they parse cleanly and are in
  range); otherwise `NOT_FOUND`.
- **`weather_lookup.py` — `WeatherLookup`**: place → reading → spoken sentence; if the
  weather provider is down, a web-search answer labelled "approximate".
- **`currency_lookup.py` — `CurrencyLookup`**: spoken names ("rupees", "$") → ISO codes
  (`domain/policies/currency_policy.py`), amount validation, provider-computed conversion.
- **`search_lookup.py` — `SearchLookup`**: news questions speak the top dated headlines;
  general questions speak the provider's answer (first sentences, with the source).
- **`ttl_cache.py` — `TtlCache`**: per-category cache (TTLs from the manifests: weather
  10 min, currency 1 h, search 15 min, geocode 24 h); hits are logged as `cache=hit`.
- **`health_service.py` — `LookupHealthService`**: concurrent probes with timeouts for
  `veda doctor` / `GET /api/lookup/health`; a probe whose secret is missing reports
  "not configured" without a network call.

- **`registry.py` — `FactProviderRegistry`**: constructed with the egress
  allow-list; `register(provider)` **refuses at registration time** if any
  of the provider's `allowed_hosts` isn't covered by the allow-list —
  fail-fast at boot rather than at first use.
- **`lookup_service.py` — `LookupService`**: `fetch(query)` re-checks
  `domain.policies.egress_policy.is_allowed()` for every host the chosen
  provider declares — a second enforcement point beyond registration
  (defense in depth), raising `EgressDeniedError` if violated.

## `service/memory/`

- **`record.py` — `Record`**: thin write-side wrapper over
  `MemoryRepositoryPort`.
- **`cross_agent_context.py` — `CrossAgentContext`**: read-side use case —
  `recent_activity(exclude_agent, limit=5)` + `format_for_prompt(...)`.
- **`knowledge_base.py` — `KnowledgeBase`**: facade over
  `KnowledgeStorePort` with an optional `on_change` callback.
- **`memory_indexer.py` — `MemoryIndexer`**: writes embeddings into a
  `VectorStorePort`; `enabled` is `True` only if an `EmbeddingPort` was
  provided, so the rest of the system is unaffected if `fastembed` isn't
  installed.
- **`semantic_recall.py` — `SemanticRecall`**: query-time counterpart —
  embeds the query, searches the vector store, filters results below
  `min_score` (default `0.3`), formats as a "RELEVANT THINGS I REMEMBER:"
  block. Also no-op without an embedding provider. This is what
  `ResponderAgent._prepare_semantic_context` calls.

## `service/privacy/` — `CaptureGate`

The single source of truth for whether the microphone is live. Composes a
required `MuteSwitchPort` plus optional `IndicatorPort`, `AudioCapturePort`,
`EventPublisherPort`, `StatusDisplayPort`.

Documented, enforced invariant: on **unmute**, the indicator must be
successfully driven to "listening" *before* audio capture starts — if the
indicator fails, the gate forces itself back to muted (fail-closed) rather
than listening with a potentially-lying light. On **mute**, audio stops
*before* the indicator updates — closing the mic is the priority on the
way down.

The hardware mute-switch callback may arrive on a non-asyncio thread (e.g.
`pynput`'s listener thread), so a `threading.Lock` guards state and event
publishing hops back to the event loop via
`asyncio.run_coroutine_threadsafe`. Each fan-out consumer is wrapped in
its own isolated `try/except` so one failing consumer never blocks the
others.

## `service/sensing/`

- **`event_bus.py` — `EventBus`** (implements `EventPublisherPort`):
  in-process pub/sub, one `asyncio.Queue` per subscriber;
  `publish()` drops to a full queue with a warning rather than blocking.
- **`rate_limiter.py` — `Debouncer` + `RateLimiter`**: ambient-narration
  specific (distinct from `guardrails/rate_limit.py`'s skill-oriented
  one). Used by `SupervisorAgent.dispatch_ambient`.
- **`sensor_registry.py` — `BaseSensor(ABC)`**: reusable `start()`/`stop()`/
  `is_running` asyncio polling lifecycle implementing `SensorPort`;
  subclasses implement `_poll_and_publish()`.

## `service/approval/` — `ApprovalBroker`

A UI-facing approval coordinator, distinct from
`guardrails/permissions.py`'s `PermissionManager` (that one is the
per-skill-call async-wait gate wired into the hook pipeline).
`request_approval()` never raises — times out to a deny, publishes an
`AmbientEvent(kind=APPROVAL_REQUEST, urgency=HIGH)` if a bus is wired.
Includes a "yolo mode" (`set_yolo`/`is_yolo`, auto-expires after
inactivity, phrase-detected via `detect_yolo_command`) and voice-based
yes/no resolution (`try_resolve_by_voice`, matches bare yes/no against
pending requests within a time window).

## `service/tasks/`

`TaskService(repo: TaskRepositoryPort)` — `add`/`add_unique`/`list`/`complete`/`delete`,
`find_open(phrase)` (best content-word match, `domain/policies/task_matching.py`) and
`purge_completed()` (tasks completed more than `COMPLETED_TASK_RETENTION_DAYS` = 7 ago;
`complete` records `completed_at`). Used by both `TasksSkill` and the `/api/tasks` routes.

## `service/prompting/` — `PromptComposer`

Assembles each stage's prompt from `PromptStorePort` text and the tool manifests under
hard token budgets (`domain/policies/token_budget_policy.py`: call 700, narrate 500,
chat 1500). Call stage = persona-lite + only the owning agent's tools + their examples
+ last 2 turns; narrate stage = persona-lite + narrate rules + question + the capped tool
result. Static text first so the KV prefix cache is reused; trimming order is oldest
history first, then the tool result. Uses the exact tokenizer when the backend offers
`count_tokens`.

## `service/voice/` — the always-on pipeline

- **`utterance_collector.py` — `UtteranceCollector`**: pure state machine
  (`silence → triggering → SPEAKING → silence, emit Utterance`), no I/O, no
  asyncio — deliberately kept unit-testable in isolation (see
  `tests/test_wake_gating.py`). `start_frames` debounces clicks/coughs;
  `silence_ms` closes the utterance; `max_duration_sec` force-closes;
  `pre_roll_frames` prepends buffered pre-speech frames so the first
  syllable isn't clipped.
- **`voice_session.py` — `VoiceSession`**: the top-level orchestrator —
  described in its own docstring as "the orchestrator the migration never
  built." Wires `AudioCapturePort`, `UtteranceCollector`, `STTPort`,
  `TTSPort`, a speaker, the `SupervisorAgent`, and `CaptureGate` together,
  with optional wake-word support (duck-typed: frame-driven like
  Porcupine, or push-to-talk like a hotkey).

  The tick loop: muted → reset any in-progress utterance and sleep (frames
  must never enter processing while muted, not merely be discarded after).
  Speaking → skip reading the mic entirely (self-echo prevention, layer
  1). Otherwise read one frame; if wake-word is configured and not armed,
  only check for the trigger; otherwise feed the frame to the
  `UtteranceCollector`.

  On a completed utterance: transcribe, drop if below `min_chars`,
  re-check mute (it may have changed mid-transcription), apply a
  word-overlap self-echo guard (layer 2 — catches the case where the
  assistant heard its own reply), then either stream-and-speak
  sentence-by-sentence (lower time-to-first-audio) or do a single blocking
  `supervisor.execute()` + speak.

  Playback discipline matters for ordering: settle delay happens *before*
  the audio buffer is drained, specifically so the tail of the assistant's
  own voice doesn't land in the buffer after the drain.

  `status()` exposes a dict snapshot (`running`, `muted`, `speaking`,
  `listening`, `turns`, `last_transcript`, `last_reply`, `armed`, ...) —
  surfaced by `/api/voice/status`.
