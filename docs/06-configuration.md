# Configuration, Constants, and Logging

## Config model: YAML-per-section, not a single file or env-var-driven

`src/core/config.py` loads configuration from **10 separate YAML files**
under `config/`, one per top-level section — not a single monolithic file,
and not primarily env-var-driven (env vars are reserved for secrets, e.g.
`PORCUPINE_ACCESS_KEY`, read directly where needed rather than flowing
through this config system).

```python
_SECTION_FILES = ("app", "inference", "embedding", "audio", "sensing",
                   "privacy", "governance", "guardrails", "agents", "skills")
```

Each section maps to `config/{name}.yaml`. `_read_section(name)` reads via
`yaml.safe_load`, returning `{}` if the file is absent — missing files
aren't an error, they just mean "use the Pydantic defaults for this
section." `load_full_config()` is memoized in a module-level
`_full_config_cache` (call `reload_full_config()` to force a re-read).
Empty/falsy values in a loaded section are filtered out before construction
so they don't override the Pydantic field defaults with `None`:

```python
AppConfig(**{k: v for k, v in raw.items() if v})
```

`ensure_dirs()` creates `DATA_DIR`, `TEMP_DIR`, `AUDIT_DIR` on boot.

**Note**: `core/config.py` imports `yaml` at module level, so `pyyaml` is a
hard runtime dependency of config loading — but it is not listed in
`pyproject.toml`'s dependencies (neither are `sqlalchemy` or `psutil`,
both also required at runtime). See the setup notes in
[00-overview.md](00-overview.md) if you're installing from scratch.

## `AppConfig` — the full schema (`schemas/config_schemas.py`)

| Section | Key fields (with defaults) |
|---|---|
| `app: AppSectionConfig` | `name="Veda"`, `port=8000`, `max_history=30`, `log_level="INFO"` |
| `inference: InferenceConfig` | `backend="ollama"`, `ollama_host`, `model_alias="qwen-wire"`, `model_path=None`, `n_ctx=4096`, `n_threads=None`, `num_batch=None`, `timeout=120`, `num_predict=512`, `temperature=0.7`, `keep_alive="30m"`, `think=False`, `routing_num_predict=64`, `summary_num_predict=256`, `verify_on_boot=True`, `warmup_on_boot=True` |
| `embedding: EmbeddingConfig` | `enabled=False`, `model_id="BAAI/bge-small-en-v1.5"`, `model_path=None`, `cache_dir="data/embeddings"`, `top_k=5`, `min_score=0.3`, `sources=["fact","summary"]` |
| `audio: AudioConfig` | `wake_word_enabled=True`, `wake_engine="openwakeword"`, `wake_hotkey="<f9>"`, `wake_window_sec=8.0`, `wake_keyword_path=None`, `wake_oww_model="hey_veda"`, `wake_oww_model_path="data/models/openwakeword/hey_veda.onnx"`, `wake_threshold=0.5`, `daemon_trigger="auto"`, `hotkey="<f8>"` (mute hotkey — separate binding from the wake hotkey), `stt_model="base.en"`, `stt_model_path=None`, `tts_engine="auto"` (`auto` = `pyttsx3` on Windows, `piper` on Linux/Pi; Piper needs `tts_model_path` pointing at a voice `.onnx` with its `.onnx.json` beside it; a missing voice or package stops voice at startup with a clear error)`, `tts_voice="zira"`, `tts_model_path=None`, `mic_device_index=None`, `speaker_device_index=None`, `voice_enabled=True`, `speak_replies=True`, `min_transcript_chars=2`, `speak_timeout_sec=60.0`, `post_speak_settle_sec=0.4`, `echo_guard=True`, `vad: VadConfig` |
| `audio.vad: VadConfig` | `engine="energy"`, `aggressiveness=2`, `frame_duration_ms=30`, `min_rms=300.0`, `speech_ratio=3.0`, `noise_adapt=0.05`, `start_frames=3`, `silence_ms=700`, `max_utterance_sec=15.0`, `pre_roll_frames=5` |
| `audio.speaker_id: SpeakerIdConfig` | `enabled=False`, `backend="resemblyzer"`, `profiles_dir="data/speaker_profiles"`, `match_threshold=0.6`, `require_known_speaker=False`, `min_enroll_seconds=12.0`, `score_window_sec=2.0` — multi-speaker voice recognition; off and purely additive by default. `backend="eagle"` (Picovoice) remains available — see `docs/voice/open-source-wake-speaker-design.md` |
| `sensing: SensingConfig` | `proactivity="medium"`, `debounce_window_sec=30.0`, `rate_limit_max=6`, `rate_limit_window_sec=60.0` |
| `audio.wake_*` | `wake_word_enabled=True`, `wake_engine` (`openwakeword` \| `none` \| `hotkey` \| `porcupine`; **config/audio.yaml sets `openwakeword`**), `wake_oww_model="hey_veda"`, `wake_oww_model_path=data/models/openwakeword/hey_veda.onnx`, `wake_threshold=0.5` (tune with `python scripts/wake_test.py`), `wake_window_sec=8.0`. A configured engine that fails to start disables voice instead of listening continuously |
| `privacy: PrivacyConfig` | `mute_switch="software"`, `start_muted=True`, `mute_gpio_pin=17`, `indicator_gpio_pin=27`, `online: OnlineConfig` |
| `privacy.online: OnlineConfig` | `enabled=True` (False = fully offline, no online tools), `allowlist=[open-meteo, geocoding-api.open-meteo, api.frankfurter.dev, open.er-api.com, api.tavily.com]`, `default_place="Hyderabad"`, `search_api_key_env="TAVILY_API_KEY"` (the *name* of the env var; the key lives in `.env`) |
| `governance: GovernanceConfig` | `enabled=False`, `provider="builtin"`, `policies_dir="tpa/governance/policies"`, `audit: GovernanceAuditConfig`, `circuit_breaker_threshold=3`, `circuit_breaker_timeout=60.0` |
| `governance.audit: GovernanceAuditConfig` | `backend="sqlite"`, `db_path="data/governance_audit.db"` |
| `guardrails: GuardrailsConfig` | `permissions: PermissionsConfig`, `validators: ValidatorsConfig`, `rate_limiting: RateLimitingConfig`, `audit: AuditConfig` |
| `guardrails.permissions` | `default_level="notify"`, `approval_timeout=60` |
| `guardrails.validators` | `block_pii_in_output=True`, `block_credentials_in_output=True`, `max_input_length=10000`, `max_output_length=50000` |
| `guardrails.rate_limiting` | `enabled=True`, `global_rpm=30`, `per_skill_rpm=10` |
| `guardrails.audit` | `enabled=True`, `log_dir="data/audit"`, `log_inputs=True`, `log_outputs=True`, `retention_days=30` |
| `agents: AgentsConfig` | `supervisor/responder/system: AgentEntry` (each `enabled=True`, `model=None` → inherits `inference.model_alias`, `description=""`, `skills=[]`; `system` defaults `skills=["terminal","file_ops"]`), `tasks` / `lookup: AgentEntry` (short descriptions used by the routing prompt), `tools_enabled=False` (True registers a specialist `ToolAgent` per owner named in `config/tools/*.yaml`; see docs/08-tool-harness.md), `llm_routing=False` (**config/agents.yaml sets True**: messages the rules don't recognise are routed by one small constrained model call instead of defaulting to chat) |
| `skills: SkillsConfig` | `terminal` (`permission_level="approve"`, extra: `working_directory`, `timeout`, `blocked_commands`, `blocked_patterns`), `file_ops` (`permission_level="notify"`, extra: `allowed_paths`, `blocked_paths`), `tasks` (`permission_level="notify"`), `custom_skills_dir="data/custom_skills"` |

The repo's actual `config/*.yaml` files override a handful of these
defaults for the demo profile — notably `inference.yaml` sets
`model_alias: qwen3-4b` (not the schema default `qwen-wire`) and
`model_path: data/Qwen3-4B-Q4_K_M.gguf`, with a comment reminding you to
run `ollama create qwen3-4b -f data/models/Modelfile.qwen3-4b` before boot
if using that model.

**Picovoice access key (opt-in backends only)**: the project's defaults
(`audio.wake_engine: openwakeword`, `audio.speaker_id.backend: resemblyzer`)
need no Picovoice key at all — see
`docs/voice/open-source-wake-speaker-design.md` for why. The key is only
needed if you explicitly switch `audio.wake_engine: porcupine` or
`audio.speaker_id.backend: eagle`, read from the `PICOVOICE_ACCESS_KEY`
env var (falling back to the older `PORCUPINE_ACCESS_KEY` name for
compatibility) — Picovoice issues one key valid for every one of their
SDKs, so a single key covers both features if you ever opt into them.
This is the one place in the config system where an env var — not a YAML
file — is the source of truth, consistent with the "env vars are for
secrets" split `core/config.py` otherwise maintains.

## Constants (`core/constants.py`)

```python
PROJECT_NAME = "Veda"
VERSION = "0.1.0"
API_PREFIX = "/api"
PROJECT_ROOT = Path(__file__).resolve().parents[2]   # repo root
DATA_DIR = PROJECT_ROOT / "data"
TEMP_DIR = DATA_DIR / "temp"
MODEL_DIR = DATA_DIR / "models"
AUDIT_DIR = DATA_DIR / "audit"
CONFIG_DIR = PROJECT_ROOT / "config"
```

Every model path, DB path, and log path elsewhere in the codebase is
resolved relative to `PROJECT_ROOT`/`DATA_DIR`, not the process's current
working directory — which is why `src/server.py` must be run with `src/`
on `PYTHONPATH` (see [04-controller-layer.md](04-controller-layer.md#running-it))
rather than relying on relative-path assumptions.

## Logging (`core/logging_config.py`)

Request-scoped IDs are propagated via a `contextvars.ContextVar`
(`_request_id`, default `"-"`), not a mutable instance attribute — the
docstring explicitly calls out that this replaces a donor concurrency bug
where a shared mutable attribute raced under asyncio. `set_request_id(id)`
sets it (called by the `request_context` middleware, see
[04-controller-layer.md](04-controller-layer.md)); a `_RequestIdFilter`
injects it into every `LogRecord` as `record.identifier`.

`configure_logging(level="INFO")` builds a logger named `"veda"` with this
format:

```
[%(asctime)s][%(levelname)-7s][%(identifier)s][%(funcName)s][%(module)s:%(filename)s].(%(lineno)d)] : %(message)s
```

A module-level `logger = configure_logging()` default instance exists so
`from core.logging_config import logger` works anywhere before
`server.py`'s `lifespan()` calls `configure_logging(cfg.app.log_level)`
with the real configured level at boot — every module in the codebase logs
through this one shared logger instance.

## Enums (`core/enums.py`)

Besides `ExceptionCode`/`ErrorMessage` (covered in
[05-exception-handling.md](05-exception-handling.md)):

- `SkillCategory(str, Enum)`: `TERMINAL`, `FILE`, `IDE`, `SEARCH`,
  `COMMUNICATION`, `CUSTOM`.
- `HookEvent(str, Enum)`: `PRE_SKILL`, `POST_SKILL`, `ON_ERROR`,
  `ON_AGENT_START`, `ON_AGENT_COMPLETE`, `ON_APPROVAL_NEEDED` — consumed by
  `service/skills/skill_runner.py` and `service/hooks/`.

All enums in this file override `__repr__`/`__str__` to return `.value`.

`inference.prompt_cache_mb` (llama_cpp only, schema default `0`, **config/inference.yaml sets `768`**): RAM for a cache of evaluated prompt prefixes shared across chat, routing and tool-call prompts. Without it every call re-reads its whole prompt because the prompts alternate; measured warm: chat 14 s -> 2.4 s, routing 5.5 s -> 2.4 s, tool decision 10 s -> 4.2 s. About 70 MB per cached ~500-token prefix; use `0` on a small device.
