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

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AppSectionConfig(BaseModel):
    """app: section — process-level basics."""

    name: str = "Veda"
    port: int = 8000
    max_history: int = 30
    # How many compressed earlier-session summaries go into every chat prompt. Each is ~100-250 tokens, and
    # prompt prefill dominates CPU latency, so small devices set this to 0-1.
    chat_summaries: int = 3
    # Persona system prompt for plain chat: "full" (persona.py, ~560 tokens) or "compact" (config/prompts/persona_chat.md,
    # ~250 tokens). The persona is re-read on every chat turn, so small CPU devices use the compact one.
    chat_persona: Literal["full", "compact"] = "full"
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
    # llama_cpp only: RAM (MB) for a cache of evaluated prompt prefixes, shared across chat / routing / tool-call
    # prompts. 0 = off. Each cached prefix of ~500 tokens costs roughly 70 MB.
    prompt_cache_mb: int = 0
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


class SpeakerIdConfig(BaseModel):
    """audio.speaker_id: sub-section — multi-speaker voice recognition.

    backend=resemblyzer (default): local PyTorch embedding model, no API
    key — see docs/voice/open-source-wake-speaker-design.md.
    backend=eagle: Picovoice Eagle, needs PICOVOICE_ACCESS_KEY; kept
    available, not installed/used by default.
    """

    enabled: bool = False
    backend: str = "resemblyzer"  # resemblyzer | eagle
    profiles_dir: str = "data/speaker_profiles"
    # Minimum similarity score to count an utterance as a known speaker.
    # Scale depends on backend — Eagle's own score and Resemblyzer's
    # cosine similarity are NOT the same scale; re-tune when switching.
    match_threshold: float = 0.6
    # When true, utterances from an unrecognized voice are dropped before
    # reaching the agent — the stray-talk filter half of this feature.
    # False by default: speaker ID only tags/personalizes, never blocks,
    # until profiles have actually been enrolled.
    require_known_speaker: bool = False
    # resemblyzer only: seconds of audio required before enroll_finish()
    # will accept a profile.
    min_enroll_seconds: float = 12.0
    # resemblyzer only: seconds of buffered audio per recognition window
    # (Resemblyzer has no per-frame API — see ResemblyzerSpeakerRecognizer).
    score_window_sec: float = 2.0


class AudioConfig(BaseModel):
    """audio: section — wake-word / STT / TTS / device selection."""

    wake_word_enabled: bool = True
    wake_engine: str = "openwakeword"  # none | hotkey | openwakeword | porcupine
    wake_hotkey: str = "<f9>"  # separate from the mute hotkey below
    wake_window_sec: float = 8.0
    wake_keyword_path: str | None = None  # .ppn file, porcupine only
    # openwakeword only. "Hey Veda" is a custom-trained model (see
    # docs/voice/open-source-wake-speaker-design.md §4.8) — not one of
    # openwakeword's bundled pretrained words — so wake_oww_model_path is
    # required in practice; there's no bundled fallback for this phrase.
    wake_oww_model: str = "hey_veda"
    wake_oww_model_path: str = "data/models/openwakeword/hey_veda.onnx"
    wake_threshold: float = 0.5
    daemon_trigger: str = "auto"  # "auto" | "wake_word" | "hotkey"
    hotkey: str = "<f8>"

    # Local STT model directory (faster-whisper format). No auto-download.
    stt_model: str = "base.en"
    stt_model_path: str | None = None
    # Per-utterance spectral gate before Whisper. Best for steady room noise.
    noise_suppression_enabled: bool = True
    noise_suppression_strength: float = Field(default=0.75, ge=0.0, le=1.0)

    tts_engine: str = "auto"  # "auto" (pyttsx3 on Windows, Piper elsewhere) | "pyttsx3" | "piper"
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
    speaker_id: SpeakerIdConfig = SpeakerIdConfig()


class SensingConfig(BaseModel):
    """sensing: section — ambient-dispatch tuning (Supervisor / debounce / rate-limit)."""

    proactivity: str = "medium"  # "conservative" | "medium" | "chatty"
    debounce_window_sec: float = 30.0
    rate_limit_max: int = 6
    rate_limit_window_sec: float = 60.0


class OnlineConfig(BaseModel):
    """privacy.online: sub-section — the network egress allow-list."""

    enabled: bool = True  # False = fully offline: no online tools are registered
    allowlist: list[str] = [
        "api.open-meteo.com", "geocoding-api.open-meteo.com", "api.frankfurter.dev", "open.er-api.com",
        "api.tavily.com",
    ]
    default_place: str = "Hyderabad"  # used when the user asks for weather without naming a place
    search_api_key_env: str = "TAVILY_API_KEY"  # env var holding the Tavily key (never stored in config)


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
    tasks: AgentEntry = AgentEntry(description="The user's to-do list: add, list, complete, delete tasks")
    lookup: AgentEntry = AgentEntry(description="Live data from the web: weather, currency rates, news, current facts")
    memory: AgentEntry = AgentEntry(description="Saves lasting facts the user states about themselves: favourites, allergies, names, routines")
    session_ttl_sec: int = 900  # session working state older than this is ignored (docs/10)


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


class PromptingConfig(BaseModel):
    """prompting: section (config/prompting.yaml). Tunable prompt budgets; a missing key keeps the dataclass default."""

    budgets: dict[str, int] = {}
    control_history_exchanges: int = Field(default=2, ge=0, le=6)
    turn_chars: int = Field(default=200, ge=40)
    user_message_chars: int = Field(default=500, ge=40)

    @field_validator("budgets")
    @classmethod
    def _known_positive_budgets(cls, budgets: dict[str, int]) -> dict[str, int]:
        from dataclasses import fields

        from domain.policies.token_budget_policy import PromptBudgets

        known = {f.name for f in fields(PromptBudgets)}
        unknown = sorted(set(budgets) - known)
        if unknown:
            raise ValueError(f"unknown prompt budget(s) {unknown}; valid: {sorted(known)}")
        bad = sorted(k for k, v in budgets.items() if v < 0)
        if bad:
            raise ValueError(f"prompt budget(s) must be >= 0: {bad}")
        return budgets


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
    prompting: PromptingConfig = PromptingConfig()
    skills: SkillsConfig = SkillsConfig()