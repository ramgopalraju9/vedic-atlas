# Service Layer

`src/service/` is the orchestration layer: the one-decision-per-turn orchestrator, skill
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

## `service/agent/` — one decision per turn

```
BaseAgent (ABC)
  ├── LLMAgent                      — + InferencePort, MemoryRepositoryPort
  │     └── ResponderAgent          — plain conversation (reached only when no tool is needed)
  ├── AssistantOrchestrator         — decides and runs every turn (does not extend LLMAgent)
  └── SupervisorAgent               — the entry point; hands every turn to the orchestrator
```

There is **no routing step**. An earlier design picked an agent from the newest message alone (keyword rules,
then a small router model) and then asked the model for a tool; the router could not see the conversation, so a
follow-up like "should I bring an umbrella?" lost its context, and a keyword regex could force a tool call the model
had correctly declined. Both were deleted (`docs/10-orchestrator-review-and-plan.md` §6g). Every turn now gets
exactly one grammar-constrained *control decode*, and tools run only through `SkillRunner`.

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
`list_all()`/`list_routable(exclude=None)`. Registered today: `responder`, `supervisor`.

### `SupervisorAgent` (`supervisor.py`)

Turn bookkeeping only (`ctx.current_agent`, `ctx.agent_chain`) and delegation to the orchestrator; sets
`result.delegated_to`. The old `route_to_agent` governance check went with routing (per-tool governance runs in
`SkillRunner`). The ambient-event path that used to live here is `service/sensing/ambient_dispatcher.py`.

### `AssistantOrchestrator` (`assistant_orchestrator.py`) and `ControlDecoder`

1. Resolve the session id **once** per turn; read the session state (`ACTIVE:` line, see `service/session/`).
2. **One control decode** (`ControlDecoder`, `PromptComposer.control_stage`): persona-lite + every tool signature +
   rules/examples (static, one cached prefix) / `ACTIVE` + `RECENT` (two complete exchanges) + `TODAY` + `USER`
   (volatile). Output is constrained by `build_control_schema` to `{needs_live_data, calls[<=3], clarification}`;
   temperature 0. Unparseable output **fails closed** (a fixed "didn't catch that" reply), never to free chat.
3. `domain/policies/dispatch_policy.resolve` turns the decision into a route: `TOOLS`, `CLARIFY`, `CHAT`, `REFUSE`
   (`needs_live_data` with no call: a fixed refusal, never free chat) or `FAIL_CLOSED`. A destructive call never
   joins a multi-call, and its target must be named in the user's own words (`destructive_policy.target_is_explicit`:
   a pronoun, ordinal or filler word is not a name) or the turn asks "Which one do you mean?".
4. `TOOLS`: each call through `SkillRunner` (`tool_execution.execute_call`; permission, rate limit, validators and
   audit hooks all apply). Reply (`domain/policies/reply_policy`): a template tool's own `spoken` sentence verbatim
   (never sent to a model); results that need phrasing share ONE content decode (`narrate`, grounding-checked); a
   failed call is a plain failure sentence.
5. `CHAT`: the `ResponderAgent`. Its `reply_veto` (`ToolUseGuard.claims_action`) replaces any reply that claims an
   action when no tool ran, also while streaming (sentence-gated).
6. Persist the turn, write the session state (validated arguments of successful, non-destructive calls only) and a
   `TurnTrace`. The same `claims_action` backstop applies on every path.

At most two model decodes per turn (control + one content/chat decode).

### `ResponderAgent` (`responder.py`) — plain chat

`build_system_prompt` returns the persona prompt (`persona.py`'s
`VEDA_SYSTEM_PROMPT`, or `persona_chat.md` when `app.chat_persona: compact`) **byte-identical every turn** — a
deliberate performance choice so the inference backend's KV-cache prefix is reused.

`build_prompt(ctx)` assembles context in least-volatile-first order (for the same cache-friendliness reason):
saved facts and the semantic-recall block → system context → recent conversation history of the **current
session** (earlier-session summaries first; each turn clipped to `prompting.turn_chars`, oldest turns dropped to
fit `PromptBudgets.chat`) → time-of-day context → voice-brevity instruction (if `ctx.from_voice`) → user message.
Tool exchanges stay in the history (the old filter that deleted them is gone). The cross-agent "recent agent
activity" block is deliberately **not** included: it held only internal routing records, which the model repeated
back as if they were the user's tasks.

The responder has no tools. `on_completion` records both sides of the turn via `ConversationManager.add_turn()`
and logs a `"chat"` memory action.

### Tools: manifests, skills, `ToolUseGuard`

Every tool is a manifest in `config/tools/*.yaml` plus a `ManifestSkill` registered in `server.py`; full design in
[08-tool-harness.md](08-tool-harness.md). Today: `get_weather`, `get_weather_forecast`, `convert_currency`,
`web_search`, `tasks`, `remember`, and the device tools `app_control`, `volume_control`, `device_status`
(`service/skills/builtin/system_control.py`; they replaced the old `SystemAgent`'s separate unconstrained planner).

- **`tool_execution.py`**: `execute_call` (one call through `SkillRunner`, logged as `[tool-call]`),
  `narrate_with_grounding` (the content stage's `narrate` task: one short completion over capped results, rejected
  and retried once if it states a number that is not in the results, the question or today's date),
  `ExecutedCall` / `TurnOutcome`.
- **`tool_use_guard.py` — `ToolUseGuard`**: built from the manifests; `claims_action(reply)` only.

## "Agent" vs. "skill" — the distinction that matters

An **agent** is a named, routable, LLM-backed persona that owns a
conversational turn end-to-end and decides *what to say*. Agents live in
`AgentRegistry`; the `SupervisorAgent` hands every turn to the orchestrator.

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
  provided, so the rest of the system is unaffected if the embedding model isn't
  staged.
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
  one). Used by `AmbientDispatcher`.
- **`ambient_dispatcher.py` — `AmbientDispatcher`**: decides how (and whether) to voice an ambient event and
  owns the proactivity level. `dispatch_ambient(event)` drops `HEARTBEAT` always, drops low-urgency events
  unless proactivity is `chatty`, applies a `Debouncer` (per `event.dedupe_key`) and a `RateLimiter` (bypassed
  for `HIGH` urgency), and mirrors summary notifications into history. `set_proactivity(level)` rebuilds the
  limiter via `proactivity_policy.rate_limit_multiplier`. Not an agent; extracted from the old supervisor.
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
hard token budgets (`domain/policies/token_budget_policy.py`, overridable in `config/prompting.yaml`:
control 3000, narrate 500, chat 1500). **Control stage** = persona-lite + every tool signature + one flagged
example per tool + rules (static, ONE cached prefix keyed only by the JSON key order, never by a tool subset) /
`ACTIVE` + `RECENT` + `TODAY` + `USER` (volatile; over budget the oldest exchange goes first). **Content stage**
(`content_stage(task, document, user_message, max_tokens=)`, tasks `narrate | summarise | draft_reply | extract`) =
persona-lite + a task-independent preamble (static, one shared prefix) / `TASK:` line + question + the document
capped at the tool's `max_result_tokens` (volatile); no tool list and no history, ever. `narrate_stage` is the
`narrate` task. Static text first so the KV prefix cache is reused. Uses the exact tokenizer when the backend
offers `count_tokens`.

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

  On a completed utterance the turn runs as a **cancellable task** (`_run_turn`) that is
  watched against the mute switch every 100 ms. Muting means *stop*: the in-flight turn is
  cancelled, a streaming model is told to stop generating (`cancel_event`), playback is cut
  off (`SpeakerPlayback.interrupt()`: `sd.stop()` + the queue is dropped), the collector and
  wake window are reset and a `turn_cancelled` event is emitted. Nothing heard before the
  mute is answered after it.

  Inside the turn: transcribe, drop if below `min_chars`, re-check mute, **drop probable
  speech-to-text artefacts** (`domain/policies/transcript_policy.artifact_reason`: stock
  Whisper hallucinations such as "subscribe to our channel", one word repeated, phrase
  loops, sound tags, symbols only, and filler heard from a very short clip; genuine speech
  such as "thank you" or "no no no" is never dropped), apply a
  word-overlap self-echo guard (layer 2 — catches the case where the
  assistant heard its own reply), then either stream-and-speak
  sentence-by-sentence (lower time-to-first-audio) or do a single blocking
  `supervisor.execute()` + speak.

  Playback discipline matters for ordering: settle delay happens *before*
  the audio buffer is drained, specifically so the tail of the assistant's
  own voice doesn't land in the buffer after the drain.

  **Wake word fails closed.** `server._build_voice_session` refuses to start the voice loop if
  a wake engine is configured but could not be built (missing model, import error) — it never
  falls back to listening continuously. Always-on listening is an explicit opt-in
  (`audio.wake_engine: none`).

  `status()` exposes a dict snapshot (`running`, `muted`, `speaking`,
  `listening`, `turns`, `last_transcript`, `last_reply`, `armed`, ...) —
  surfaced by `/api/voice/status`.
