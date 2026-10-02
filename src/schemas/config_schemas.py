"""Pydantic configuration section models.

Donor: veda/config.py (337 real lines, not the 264 FILE_MAP estimated —
read in full). Split per migration rule 3: this module holds only the
declarative Pydantic shapes; loading/caching lives in core/config.py, the
actual values live in config/*.yaml (one file per section below).

Section-by-section changes from the donor:
  - `VedaConfig` -> `AppSectionConfig`: dropped `model`/`voice` (a cloud
    model name and a Windows SAPI voice name — both superseded by
    `InferenceConfig`/`AudioConfig`).
  - `BrainConfig` -> `InferenceConfig`: dropped `backend` cloud values
    (`claude`/`copilot`/`hybrid_cloud` — see tpa/inference/factory.py's
    hard reject), all `copilot_*` fields, all `warm_pool_*` fields (no CLI
    cold-start to hide — Ollama's own `keep_alive` covers this). Added
    `model_path`, `n_ctx`, `n_threads` for the llama.cpp backend.
  - `VoiceConfig` split into `AudioConfig` (the voice I/O stack: wake
    word, STT, TTS, mic/speaker device selection) and `SensingConfig`
    (ambient-dispatch tuning: proactivity default, debounce/rate-limit
    windows — the constructor args `SupervisorAgent`/`Debouncer`/
    `RateLimiter` already take).
  - `VisionConfig` / `TeamsConfig`: dropped entirely — both out of scope.
  - `AgentsConfig`/`AgentEntry`: dropped `research`/`coder`/`communicator`
    entries — none of those agents exist in this build (no web-search
    agent, no code-runner, no Teams). `chat` renamed `responder` to match
    the actual registered agent name (service/agent/responder.py).
  - `SkillsConfig`/`SkillEntry`: dropped `vision`/`vscode`/`pycharm`/
    `intellij`/`teams` entries (skills that don't exist); `web_search`
    dropped too — there's no skill wrapping `service/lookup/` yet.
  - `GovernanceConfig`: flattened the donor's nested `sre:` sub-section
    into `circuit_breaker_threshold`/`circuit_breaker_timeout` directly,
    matching `service/governance/factory.py`'s `getattr(config, ...)`
    calls. `policies_dir` default now points at `tpa/governance/policies`.
  - `GuardrailsConfig`: unchanged in shape — still real, still used by
    `service/guardrails/*`.
  - `HooksConfig` dropped entirely: the donor's version was a list of
    `"builtin:x"` name strings with no dynamic loader behind it — there
    is exactly one fixed set of built-in hooks
    (`service/hooks/dispatcher.py`), and `server.py` wires them directly.
    A config file whose only possible values are the same five hardcoded
    strings adds a config surface with no real flexibility behind it.
  - New: `PrivacyConfig` (`mute_gpio_pin`, `indicator_gpio_pin` for the Pi
    hardware profile; `online.allowlist` for the egress allow-list shared
    by `tpa/online/` and `service/lookup/`) — none of this existed in the
    donor, which had no network allow-list or GPIO concept at all.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AppSectionConfig(BaseModel):
    """app: section — process-level basics."""

    name: str = "Veda"
    port: int = 8000
    max_history: int = 30
    log_level: str = "INFO"


class InferenceConfig(BaseModel):
    """inference: section — local LLM backend selection (ADR-001)."""

    backend: str = "ollama"  # "ollama" | "llama_cpp"
    ollama_host: str = "http://127.0.0.1:11434"
    model_alias: str = "qwen-wire"
    model_path: str | None = None  # required when backend == "llama_cpp"
    n_ctx: int = 4096
    n_threads: int | None = None
    num_batch: int | None = None  # Ollama prompt-eval batch size (default 512)
    timeout: int = 120

    # Generation budget. `num_predict` is the strongest latency control on
    # CPU-only hardware — without it Ollama generates until the model
    # decides to stop, which a base model may never do.
    num_predict: int = 512
    temperature: float = 0.7
    keep_alive: str = "30m"

    # Reasoning-model thinking blocks. Measured on Qwen3-8B: 16x slower
    # with thinking on, for identical visible output. Ignored by models
    # that don't support it.
    think: bool = False

    # Shorter budgets for non-conversational calls — routing and
    # summarising need a handful of tokens, not a full reply.
    routing_num_predict: int = 64
    summary_num_predict: int = 256

    # Validate the model is importable at boot rather than 404-ing on the
    # first user turn. Disable only for tests with no Ollama running.
    verify_on_boot: bool = True
    warmup_on_boot: bool = True


class EmbeddingConfig(BaseModel):
    """embedding: section — local embedding model used for context retrieval."""

    enabled: bool = False
    model_id: str = "BAAI/bge-small-en-v1.5"
    model_path: str | None = None
    cache_dir: str | None = "data/embeddings"
    top_k: int = 5
    min_score: float = 0.3  # minimum cosine similarity to inject a memory
    sources: list[str] = ["fact", "summary"]  # which memory sources recall searches


class VadConfig(BaseModel):
    """audio.vad: sub-section — voice activity detection tuning."""

    # "energy" needs only numpy and works everywhere (default).
    # "webrtc" is more precise but needs a C toolchain to build on 3.13+.
    engine: str = "energy"

    # webrtc only: 0-3, higher filters more aggressively.
    aggressiveness: int = 2
    # WebRTC accepts only 10/20/30 ms frames; energy accepts any.
    frame_duration_ms: int = 30

    # energy only: absolute floor, and how far above ambient counts as speech.
    min_rms: float = 300.0
    speech_ratio: float = 3.0
    noise_adapt: float = 0.05

    # Consecutive speech frames before an utterance opens — rejects clicks.
    start_frames: int = 3
    # Trailing silence that ends an utterance.
    silence_ms: int = 700
    # Hard stop so background noise can't hold the pipeline open forever.
    max_utterance_sec: float = 15.0
    # Frames kept before speech is confirmed, so the first syllable survives.
    pre_roll_frames: int = 5


class AudioConfig(BaseModel):
    """audio: section — wake-word / STT / TTS / device selection."""

    wake_word_enabled: bool = True
    wake_engine: str = "hotkey"  # none | hotkey | porcupine
    wake_hotkey: str = "<f9>"  # separate from the mute hotkey below
    wake_window_sec: float = 8.0
    wake_keyword_path: str | None = None  # .ppn file, porcupine only
    daemon_trigger: str = "auto"  # "auto" | "wake_word" | "hotkey"
    hotkey: str = "<f8>"

    # Local STT model directory (faster-whisper format). No auto-download.
    stt_model: str = "base.en"
    stt_model_path: str | None = None

    tts_engine: str = "pyttsx3"  # "pyttsx3" | "piper"
    tts_voice: str = "zira"
    tts_model_path: str | None = None  # required when tts_engine == "piper"

    mic_device_index: int | None = None
    speaker_device_index: int | None = None

    # Start the always-on loop at boot. The capture gate still governs
    # whether the mic actually opens, so this is safe to leave on.
    voice_enabled: bool = True
    speak_replies: bool = True
    # Ignore transcripts shorter than this — filters STT noise artefacts.
    min_transcript_chars: int = 2

    # Max wait for playback to finish before reopening the mic. A stuck audio
    # device must not wedge the loop forever.
    speak_timeout_sec: float = 60.0
    # Quiet gap after playback before the mic buffer is discarded, so the tail
    # of our own voice doesn't survive the drain.
    post_speak_settle_sec: float = 0.4
    # Drop transcripts that are mostly our own last reply (self-echo).
    echo_guard: bool = True

    vad: VadConfig = VadConfig()


class SensingConfig(BaseModel):
    """sensing: section — ambient-dispatch tuning (Supervisor / debounce / rate-limit)."""

    proactivity: str = "medium"  # "conservative" | "medium" | "chatty"
    debounce_window_sec: float = 30.0
    rate_limit_max: int = 6
    rate_limit_window_sec: float = 60.0


class OnlineConfig(BaseModel):
    """privacy.online: sub-section — the network egress allow-list."""

    allowlist: list[str] = ["api.open-meteo.com", "api.duckduckgo.com", "api.frankfurter.app"]


class PrivacyConfig(BaseModel):
    """privacy: section — mute/indicator hardware + network egress."""

    mute_switch: str = "software"  # software | keyboard | hid | gpio
    start_muted: bool = True  # boot muted; explicit opt-in required to listen
    mute_gpio_pin: int = 17
    indicator_gpio_pin: int = 27
    online: OnlineConfig = OnlineConfig()


class GovernanceAuditConfig(BaseModel):
    """governance.audit: sub-section."""

    backend: str = "sqlite"  # "sqlite" | "null"
    db_path: str = "data/governance_audit.db"


class GovernanceConfig(BaseModel):
    """governance: section — plug-and-play policy enforcement."""

    enabled: bool = False
    provider: str = "builtin"  # "builtin" | "null" ("agt" is a hard reject)
    policies_dir: str = "tpa/governance/policies"
    audit: GovernanceAuditConfig = GovernanceAuditConfig()
    circuit_breaker_threshold: int = 3
    circuit_breaker_timeout: float = 60.0


class PermissionsConfig(BaseModel):
    default_level: str = "notify"
    approval_timeout: int = 60


class ValidatorsConfig(BaseModel):
    block_pii_in_output: bool = True
    block_credentials_in_output: bool = True
    max_input_length: int = 10000
    max_output_length: int = 50000


class RateLimitingConfig(BaseModel):
    enabled: bool = True
    global_rpm: int = 30
    per_skill_rpm: int = 10


class AuditConfig(BaseModel):
    enabled: bool = True
    log_dir: str = "data/audit"
    log_inputs: bool = True
    log_outputs: bool = True
    retention_days: int = 30


class GuardrailsConfig(BaseModel):
    """guardrails: section."""

    permissions: PermissionsConfig = PermissionsConfig()
    validators: ValidatorsConfig = ValidatorsConfig()
    rate_limiting: RateLimitingConfig = RateLimitingConfig()
    audit: AuditConfig = AuditConfig()


class AgentEntry(BaseModel):
    """Single agent definition under agents: section."""

    enabled: bool = True
    # None = inherit inference.model_alias. Per-agent models only make sense
    # once more than one model is imported; ADR-008 specifies a single SLM.
    model: str | None = None
    description: str = ""
    skills: list[str] = []


class AgentsConfig(BaseModel):
    """agents: section. Only agents that actually exist in this build."""

    supervisor: AgentEntry = AgentEntry(description="Routes requests to specialist agents")
    responder: AgentEntry = AgentEntry(description="General conversation, Q&A, brainstorming")
    system: AgentEntry = AgentEntry(
        description="Device actions: open/close apps, volume, running processes",
        skills=["terminal", "file_ops"],
    )
    tools_enabled: bool = False  # when true, the responder may call its skills
    max_tool_iterations: int = 3


class SkillEntry(BaseModel):
    """Single skill definition under skills: section."""

    enabled: bool = True
    permission_level: str = "approve"
    extra: dict[str, Any] = {}


class SkillsConfig(BaseModel):
    """skills: section. Only skills that actually exist in this build."""

    terminal: SkillEntry = SkillEntry(
        permission_level="approve",
        extra={
            "working_directory": "~",
            "timeout": 30,
            "blocked_commands": ["rm -rf /", "format", "del /s /q", "shutdown", ":(){:|:&};:"],
            "blocked_patterns": [r"rm\s+-rf\s+/"],
        },
    )
    file_ops: SkillEntry = SkillEntry(
        permission_level="notify",
        extra={
            "allowed_paths": ["~", "."],
            "blocked_paths": ["C:\\Windows", "C:\\Program Files", "/etc", "/usr"],
        },
    )
    tasks: SkillEntry = SkillEntry(permission_level="notify")
    custom_skills_dir: str = "data/custom_skills"


class AppConfig(BaseModel):
    """Complete application configuration combining all sections."""

    app: AppSectionConfig = AppSectionConfig()
    inference: InferenceConfig = InferenceConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    audio: AudioConfig = AudioConfig()
    sensing: SensingConfig = SensingConfig()
    privacy: PrivacyConfig = PrivacyConfig()
    governance: GovernanceConfig = GovernanceConfig()
    guardrails: GuardrailsConfig = GuardrailsConfig()
    agents: AgentsConfig = AgentsConfig()
    skills: SkillsConfig = SkillsConfig()