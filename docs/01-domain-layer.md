# Domain Layer

`src/domain/` is the innermost layer: pure data shapes, pure decision
functions, and interface contracts. Nothing here performs I/O, imports a
third-party SDK, or imports anything from `service/`, `controller/`, or
`tpa/`. This is what makes it safe for every other layer to depend on it
without creating a cycle.

Five subfolders: `entities/`, `value_objects/`, `ports/`, `policies/`,
`events/`.

## Entities vs. value objects

The codebase draws a consistent distinction:

- **`entities/`** — plain `@dataclass` (mutable), used for things with
  identity, lifecycle, or that get mutated in place as they flow through a
  request (e.g. `AgentContext` accumulates `agent_chain`, `skill_results`,
  etc. as it's passed between agents). The one exception is
  `PolicyDecision`, which is `frozen=True` despite living in `entities/` —
  a decision, once made, shouldn't be mutated.
- **`value_objects/`** — `@dataclass(frozen=True)` or `str, Enum` (all three
  enums — `PermissionLevel`, `ProactivityLevel`, `Urgency` — additionally
  override `__str__` to return `.value`). Pure, immutable data shapes with
  no identity. The one deliberate exception is `AudioWindow`, which is a
  plain (non-frozen) dataclass despite living here — its docstring explains
  it's in-memory-only and was never meant to be persisted or logged, so
  immutability wasn't the point; it lives in `value_objects/` for its
  dependency-free-ness, not its frozen-ness.

### `domain/entities/`

| Class | Fields |
|---|---|
| `AgentContext` | `request_id` (auto `uuid4().hex[:12]`), `timestamp`, `user_message`, `system_context`, `from_voice`, `agent_chain: list[str]`, `current_agent`, `skill_results: list[dict]`, `conversation_history: list[dict]`, `knowledge_context`, `pending_approvals: list[dict]`, `approved_actions: list[str]`, `metadata: dict` |
| `AgentProfile` | `name`, `description`, `model_alias`, `skills: tuple[str,...]`, `system_prompt_extra`, `enabled` |
| `AgentResult` | `agent_name`, `response`, `skill_calls: list[dict]`, `delegated_to: str\|None`, `metadata: dict` |
| `ApprovalRequest` | `request_id`, `action_name`, `arguments: dict`, `reason`, `created_at`, `timeout_sec=60`, `status="pending"` (`pending\|approved\|denied\|timed_out`), `resolved_via: str\|None` (`ui\|voice\|timeout`). Method: `age_sec(now) -> float` |
| `AuditEntry` | `event_type`, `agent`, `action`, `context: dict`, `decision: PolicyDecision`, `timestamp` (UTC), `hash=""` (filled in by the audit-sink adapter) |
| `CaptureState` | `muted`, `mute_source`, `since` |
| `Turn` | `id: int\|None`, `session_id`, `role`, `content`, `created_at`, `summarized=False` |
| `ConversationSummary` | `id: int\|None`, `session_id`, `from_ts`, `to_ts`, `content`, `turn_count`, `created_at` |
| `FactAnswer` | `provider_id`, `category`, `text`, `sources: tuple[str,...]`, `fetched_at: datetime\|None` |
| `FactQuery` | `category`, `params: dict`, `requested_at: datetime\|None` |
| `MemoryRecord` | `agent_name`, `action`, `context: dict`, `user_message`, `created_at: datetime\|None` |
| `PolicyDecision` (**frozen**) | `allowed=True`, `action="allow"` (`allow\|deny\|audit\|block`), `rule_name: str\|None`, `reason`, `audit_entry: dict` |
| `SkillDefinition` | `name`, `description`, `permission_level=APPROVE`, `input_schema: dict`, `enabled=True`. Method: `to_prompt_block() -> str` |
| `SkillResult` | `skill_name`, `success`, `output: Any=None`, `error: str\|None`, `metadata: dict` |
| `Task` | `id: int\|None`, `title`, `done=False`, `notes`, `due_at: datetime\|None`, `created_at` |
| `Utterance` | `audio: AudioWindow`, `started_at`, `duration_sec`, `frame_count`, `truncated=False` |

### `domain/value_objects/`

| Type | Shape |
|---|---|
| `AudioWindow` (not frozen — see note above) | `pcm: bytes`, `sample_rate: int`, `channels: int`, `started_at: datetime`, `duration_sec: float` |
| `EgressTarget` (frozen) | `host`, `category`, `reason=""` |
| `Embedding` (frozen) | `vector: tuple[float,...]`, `model_id`. Property: `dim -> int` |
| `MemoryHit` (frozen) | `source` (`fact\|summary\|...`), `ref_id`, `text`, `score` |
| `ModelSpec` (frozen) | `model_id`, `quantization`, `n_ctx=4096`, `n_threads: int\|None` |
| `PermissionLevel(str, Enum)` | `AUTO`, `NOTIFY`, `APPROVE` |
| `ProactivityLevel(str, Enum)` | `CONSERVATIVE`, `MEDIUM`, `CHATTY` |
| `ToolCall` (frozen) | `name`, `args: dict` |
| `Transcript` (frozen) | `text`, `confidence`, `language="en"`, `is_final=True`, `duration_sec=0.0` |
| `Urgency(str, Enum)` | `LOW`, `NORMAL`, `HIGH` |

## `domain/policies/` — pure business-rule functions

No classes, no I/O, no implicit clock access (every function that needs
"now" takes it as a parameter, which keeps them trivially testable).

| File | Function(s) | What it decides |
|---|---|---|
| `egress_policy.py` | `is_allowed(target, allow_list) -> bool`; `is_audio_payload(content_type) -> bool` | Host allow-list check (`localhost`/`127.0.0.1` always allowed); rejects audio content-types |
| `permission_policy.py` | `requires_approval(level)`; `requires_notification(level)`; `proceeds_immediately(level)` | Maps a `PermissionLevel` to one of three gating behaviors |
| `proactivity_policy.py` | `rate_limit_multiplier(level) -> int` | `2` for `CHATTY`, `1` otherwise |
| `redaction_policy.py` | `find_pii(content) -> str\|None`; `find_credential(content) -> str\|None` | Regex match for SSN/credit-card/PAN/Aadhaar, and API-key/password/secret/AWS/OpenAI/GitHub-PAT patterns |
| `retention_policy.py` | `is_expired_agent_memory(created_at, now) -> bool`; `is_expired_user_fact(...) -> bool` (always `False`) | `AGENT_MEMORY_RETENTION_DAYS = 10` rolling expiry; user facts never auto-expire |
| `routing_policy.py` | `pick_agent(message, candidates, default) -> str` (never `None`) | Keyword-overlap between message and each `AgentProfile.description`, highest score wins, falls back to `default` |
| `session_boundary_policy.py` | `is_same_session(last_activity_at, now, session_gap_min=30) -> bool` | `DEFAULT_SESSION_GAP_MIN = 30` — true if gap ≤ threshold |

These are consumed by `service/` (see [03-service-layer.md](03-service-layer.md))
and, in the egress case, double-enforced: once at `FactProviderRegistry`
registration time and again at every `LookupService.fetch()` call.

## `domain/events/`

| File | Shape |
|---|---|
| `event_kind.py` | `EventKind(str, Enum)`: `OBSERVATION`, `NOTIFICATION`, `HEARTBEAT`, `REMINDER`, `SYSTEM`, `APPROVAL_REQUEST` |
| `ambient_event.py` | `AmbientEvent`: `kind`, `description`, `urgency=NORMAL`, `source`, `dedupe_key`, `payload: dict`, `event_id` (auto), `timestamp` (auto) |
| `approval_event.py` | `ApprovalEvent`: `request_id`, `status`, `resolved_via: str\|None`, `occurred_at` |
| `capture_event.py` | `CaptureEvent`: `muted`, `previous_muted`, `source`, `changed_at` |

`AmbientEvent` is the payload type for the whole ambient/narration system —
see `EventBus` and `SupervisorAgent.dispatch_ambient` in
[03-service-layer.md](03-service-layer.md).

## `domain/ports/` — the contract layer

Every port is a `typing.Protocol` decorated `@runtime_checkable`. None
share a common base beyond `Protocol` itself. Naming convention: `<Noun>Port`,
except `GovernanceProvider` (no `Port` suffix). This is the complete list —
cross-reference [02-tpa-adapters.md](02-tpa-adapters.md) for what implements
each one.

| Port | Key methods | Sync/Async |
|---|---|---|
| `AudioCapturePort` | `sample_rate`, `channels` (props); `start()`; `stop()`; `read(timeout=1.0) -> AudioWindow\|None` | sync |
| `AuditSinkPort` | `record(entry) -> AuditEntry`; `recent(limit=20)`; `query(*, event_type=None, agent=None, limit=100)`; `stats()`; `verify_chain()` | sync |
| `ClockPort` | `now() -> datetime` | sync |
| `ConversationRepositoryPort` | `current_session_id()`; `add_turn(turn) -> int`; `recent_turns(limit=30, only_unsummarized=True)`; `add_summary(summary) -> int`; `recent_summaries(limit=3)`; `un_summarized_by_session(session_id)`; `sessions_with_unsummarized()`; `mark_summarized(turn_ids)`; `delete_summarized_before(cutoff)` | sync |
| `EmbeddingPort` | `model_id` (prop); `async embed(text) -> Embedding` | async |
| `EventPublisherPort` | `async publish(event) -> int`; `subscribe(maxsize=100) -> asyncio.Queue`; `unsubscribe(q)` | mixed |
| `FactProviderPort` | `category`, `allowed_hosts` (props); `async fetch(query) -> FactAnswer` | async fetch |
| `GovernanceProvider` | `name` (prop); `check_action(action, context) -> PolicyDecision`; `check_pattern(text) -> list[str]`; `async audit(entry)`; `is_healthy(backend)`; `record_success(backend)`; `record_failure(backend)` | mixed |
| `IndicatorPort` | `set_muted(muted) -> None` | sync |
| `InferencePort` | `name` (prop); `async complete(prompt, system="", model=None, timeout=None) -> str`; `stream(...) -> AsyncIterator[str]`. Also declares `InferenceTimeoutError(RuntimeError)` in the same file | async |
| `KnowledgeStorePort` | `add_fact`; `remove_fact(index) -> bool`; `list_facts()`; `get_context()` | sync |
| `MemoryRepositoryPort` | `record(agent_name, action, context=None, user_message="") -> int`; `recent(limit=5)`; `recent_cross_agent(exclude=None, limit=5)`; `last_action(agent_name=None)`; `prune() -> int` | sync |
| `MuteSwitchPort` | `source_name` (prop); `is_muted() -> bool`; `subscribe(on_change: Callable[[CaptureEvent], None])` | sync, callback-based |
| `NotificationPort` | `notify(title, message, urgency=NORMAL) -> None` | sync |
| `SensorPort` | `start()`; `async stop()`; `is_running` (prop) | mixed |
| `StatusDisplayPort` | `rows`, `cols` (props); `show(lines: tuple[str,...])` | sync |
| `STTPort` | `async transcribe(audio) -> Transcript` | async |
| `SystemControlPort` | `open_app/close_app/focus_app(name) -> (bool,str)`; `battery_info() -> dict`; `get_volume()/set_volume(level)`; `mute(on)/is_muted()`; `list_running(name_filter=None)`; `top_processes(limit=5)` | sync |
| `TaskRepositoryPort` | `add(task) -> int`; `list(include_done=False)`; `get(task_id)`; `set_done(task_id, done=True) -> bool`; `delete(task_id) -> bool` | sync |
| `TTSPort` | `sample_rate` (prop); `synthesize(text, voice=None) -> AsyncIterator[bytes]` | async generator |
| `VoiceActivityPort` | `frame_duration_ms` (prop); `is_speech(frame) -> bool` | sync |
| `VectorStorePort` | `upsert(*, source, ref_id, model_id, vector, text)`; `search(*, vector, model_id, top_k=5, sources=None) -> list[MemoryHit]`; `has(*, source, ref_id, model_id)`; `delete(*, source, ref_id)`; `clear(source)` | sync, kwonly args |
| `WakeWordPort` | `frame_length`, `sample_rate` (props); `process(frame: bytes) -> bool` | sync |
| `SpeakerRecognitionPort` | `frame_length`, `sample_rate`, `speaker_names` (props); `process(frame: bytes) -> list[float]` (one score per enrolled speaker, order matching `speaker_names`); `reload_profiles() -> None` | sync |
| `SpeakerEnrollmentPort` | `frame_length`, `sample_rate` (props); `enroll_feed(frame: bytes) -> tuple[float, str]`; `enroll_reset()`; `enroll_finish(speaker_name: str)`; `list_enrolled() -> list[str]`; `delete_enrolled(speaker_name: str) -> bool` | sync |

That's 22 ports (20 original + `SpeakerRecognitionPort`/`SpeakerEnrollmentPort`,
added for multi-speaker voice recognition). Every one of them has a
concrete implementation in `tpa/` — see
[02-tpa-adapters.md](02-tpa-adapters.md) for the full mapping.
