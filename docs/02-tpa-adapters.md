# tpa/ — Third-Party Adapters

`src/tpa/` is the only layer permitted to import third-party SDKs and talk
to the outside world: hardware (GPIO, HID, audio devices), local model
runtimes (Ollama, llama.cpp, faster-whisper, Piper, onnxruntime), SQLite, and
the one allow-listed HTTP client. Every class here implements exactly one
`domain.ports.*` Protocol (see [01-domain-layer.md](01-domain-layer.md)
for the contracts) and nothing in `service/` ever imports from here
directly — adapters only reach `service/` by being constructed in
`src/server.py` and injected as a port-typed dependency.

## Port → adapter map

| Port | Adapter(s) | Folder |
|---|---|---|
| `AudioCapturePort` | `SoundDeviceCapture` | `tpa/audio/` |
| `VoiceActivityPort` | `EnergyVadDetector` (default), `WebRtcVadDetector` | `tpa/audio/` |
| — (playback, no dedicated port) | `SpeakerPlayback` | `tpa/audio/` |
| `KnowledgeStorePort` | `JsonKnowledgeStore` | `tpa/filestore/` |
| `AuditSinkPort` | `SqliteAuditSink`, `NullAuditSink` | `tpa/governance/` |
| `IndicatorPort` | `GpioIndicator`, `BlinkStickIndicator`, `SoftwareIndicator` | `tpa/hardware/` |
| `MuteSwitchPort` | `GpioMuteSwitch`, `HidMuteSwitch`, `KeyboardMuteFallback`, `SoftwareMuteSwitch` | `tpa/hardware/` |
| `StatusDisplayPort` | `CharDisplay` | `tpa/hardware/` |
| `EmbeddingPort` | `OnnxEmbeddingProvider` | `tpa/inference/` |
| `InferencePort` | `OllamaClient`, `LlamaCppClient` (via `factory.build_inference_client`) | `tpa/inference/` |
| `NotificationPort` | `DesktopNotifier` | `tpa/notifications/` |
| — (sound alerts, no dedicated port) | `SoundPlayer` | `tpa/notifications/` |
| `FactProviderPort` | `WeatherProvider`, `SearchProvider`, `FxProvider` | `tpa/online/providers/` |
| — (egress enforcement, no dedicated port) | `AllowListedHttpClient` | `tpa/online/` |
| `ConversationRepositoryPort` | `ConversationRepository` | `tpa/persistence/` |
| `MemoryRepositoryPort` | `AgentMemoryRepository` | `tpa/persistence/` |
| `STTPort` | `FasterWhisperProvider` | `tpa/stt/` |
| `SystemControlPort` | `WindowsSystemAdapter`, `LinuxSystemAdapter` | `tpa/system/` |
| `TTSPort` | `PiperProvider` (default), `Pyttsx3Provider` (offline fallback) | `tpa/tts/` |
| `WakeWordPort` | `OpenWakeWordEngine` (default), `HotkeyWakeWord` (fallback, not headless-safe), `PorcupineWakeWord` (Picovoice, opt-in) | `tpa/wake_word/` |
| `SpeakerRecognitionPort` | `ResemblyzerSpeakerRecognizer` (default), `EagleSpeakerRecognizer` (Picovoice, opt-in) | `tpa/speaker/` |
| `SpeakerEnrollmentPort` | `ResemblyzerSpeakerEnroller` (default), `EagleSpeakerEnroller` (Picovoice, opt-in) | `tpa/speaker/` |

`ClockPort`, `SensorPort`, and `VectorStorePort` are declared in `domain/ports/`
but their concrete implementations live outside `tpa/` (e.g.
`SqliteVectorStore` is wired directly in `server.py`) — not every port's
adapter is necessarily under `tpa/`, but every adapter under `tpa/`
implements a port.

## `tpa/audio/` — capture, VAD, playback

- **`sounddevice_capture.py` — `SoundDeviceCapture`**: wraps
  `sounddevice.RawInputStream`. `read()` wraps raw PCM in a domain
  `AudioWindow` so nothing above this adapter ever sees a vendor-specific
  buffer type. Fixed `SAMPLE_RATE=16_000`, `CHANNELS=1`, `DTYPE="int16"`.
  Many USB microphones accept only 44.1/48 kHz when opened directly ("Invalid sample rate"), so if the 16 kHz
  open is refused it re-opens at the device's own rate (device default, then 48000, then 44100) and converts every
  block to 16 kHz with `tpa/audio/resampler.py` (`Int16BlockResampler`, scipy `resample_poly` with the previous
  block as context); it logs `[mic] cannot open at 16000 Hz ...; opened at 48000 Hz and converting`. If no rate
  works the original error is raised. Check a microphone with `python scripts/mic_check.py` (server stopped).
- **`energy_vad.py` — `EnergyVadDetector`** (the default VAD): adaptive RMS
  energy thresholding — tracks a running noise floor from quiet frames,
  fires when a frame is meaningfully louder than ambient. Chosen as default
  over WebRTC's VAD because `webrtcvad` ships no wheel for Python 3.13+ and
  needs a C compiler to build, which a locked-down corp laptop or a fresh
  Pi image typically lacks. Trade-off: detects *sound*, not *speech* — a
  door slam can open an utterance (costs one wasted STT pass, filtered by
  `VoiceSession`'s `min_chars` check).
- **`webrtc_vad.py` — `WebRtcVadDetector`**: wraps `webrtcvad`, a pure-C GMM
  classifier — no model file, sub-millisecond per frame. Validates sample
  rate (8/16/32/48kHz) and frame duration (10/20/30ms) at construction.
  Interchangeable with `EnergyVadDetector` via config — both expose the
  identical `frame_duration_ms`/`expected_frame_bytes`/`is_speech(frame)`
  shape.
- **`ring_buffer.py` — `RingBuffer`**: fixed-capacity `deque` of raw PCM
  chunks, in-memory only (never touches disk) — explicit implementation
  of the "audio stays in memory only" rule.
- **`playback.py` — `SpeakerPlayback`**: one worker thread serializes PCM
  playback via `sounddevice.play()` so sentence-by-sentence TTS queues
  cleanly instead of overlapping. `wait_done()` exists specifically to fix
  a real feedback-loop bug: estimating playback duration from PCM length
  and re-opening the mic too early caused the assistant to transcribe and
  respond to its own voice.

## `tpa/filestore/` and `tpa/persistence/`

- **`json_knowledge_store.py` — `JsonKnowledgeStore`**: flat JSON-file
  persistence for user-taught facts (`{"fact", "added"}` objects).
- **`yaml_tool_manifest_store.py` — `YamlToolManifestStore`**: implements
  `ToolManifestStorePort`; loads and validates `config/tools/*.yaml` through
  `schemas/tool_manifest_schema.py`. An invalid or duplicate manifest raises
  `AppException(CONFIG_ERROR)` at boot.
- **`file_prompt_store.py` — `FilePromptStore`**: implements `PromptStorePort`;
  reads versioned prompt text from `config/prompts/<name>.md`, cached after the first read.
- **`persistence/session.py`**: SQLAlchemy engine + `SessionLocal` factory.
  SQLite by default (`DATABASE_URL` env var overrides), with
  `check_same_thread=False` so background threads (audio capture, ambient
  sensing) can share the connection safely.
- **`persistence/models/`**: ORM row classes (`AgentMemoryRow`,
  `ConversationTurnRow`, `ConversationSummaryRow`) — named with a `Row`
  suffix specifically to stay visually distinct from the domain entities
  (`MemoryRecord`, `Turn`, `ConversationSummary`) they map to/from.
- **`persistence/mappers.py`**: the *only* place ORM rows get translated to
  domain entities — `turn_row_to_entity`, `summary_row_to_entity`,
  `memory_row_to_entity`. No `Mapped[...]` SQLAlchemy type crosses this
  boundary into `service/`.
- **`persistence/repositories/`**: `ConversationRepository` (implements
  `ConversationRepositoryPort` — including the real 30-minute session-gap
  logic, delegating the actual gap *decision* to the pure
  `domain.policies.session_boundary_policy.is_same_session`) and
  `AgentMemoryRepository` (implements `MemoryRepositoryPort`, prunes
  records older than `AGENT_MEMORY_RETENTION_DAYS` on every write).
- **`persistence/migrations.py`**: `init_tables()` — creates tables via
  `Base.metadata.create_all(engine)` if they don't exist; called once at
  boot in `server.py`'s `lifespan()`. `create_all` never alters an existing
  table, so columns added later (e.g. `tasks.completed_at`) are applied by
  `_add_missing_columns()` (idempotent SQLite `ADD COLUMN`).
- **`persistence/models/turn_trace.py` + `repositories/trace_repository.py`**:
  `TurnTraceRow` / `SqliteTraceRepository` (implements `TraceRepositoryPort`) —
  one row per tool turn (calls, results, timings, prompt tokens), purged after
  `TURN_TRACE_RETENTION_DAYS` (14).

## `tpa/governance/`

- **`sqlite_audit_sink.py` — `SqliteAuditSink`**: tamper-evident,
  hash-chained audit log. Each row's hash = `SHA-256(previous_hash +
  row_data)`, forming an append-only chain; `verify_chain()` walks every
  row and recomputes the chain to detect tampering. `NullAuditSink` is the
  no-op counterpart used when governance/audit is disabled.
- **`policies/*.yaml`**: the actual policy rule files loaded by
  `service/governance/policy_evaluator.py`'s `PolicyEvaluator` —
  `agent_routing.yaml`, `prompt_safety.yaml`, `system_actions.yaml`,
  `tool_gating.yaml`. Each rule has a `condition` (field/operator/value),
  an `action` (`allow|deny|audit`), and a `priority` (higher evaluated
  first). `prompt_safety.yaml` and `system_actions.yaml` also carry a
  `blocked_patterns` list of regexes checked via `check_pattern()`.

## `tpa/hardware/`

Mute switches (`MuteSwitchPort`) and listening indicators (`IndicatorPort`)
exist in parallel hardware/software tiers, selected by config
(`privacy.mute_switch`):

- **`gpio_mute_switch.py` — `GpioMuteSwitch`**: physical GPIO button
  (pull-up, active-low), debounced in software (20ms).
- **`hid_mute_switch.py` — `HidMuteSwitch`**: polled USB HID button (for
  the laptop profile) — polls on a background thread since most simple HID
  buttons don't push interrupts to Python.
- **`keyboard_mute_fallback.py` — `KeyboardMuteFallback`**: global hotkey
  toggle via `pynput`. Explicitly labeled a *weaker-trust* fallback in its
  docstring — a software switch can be bypassed by any code in the
  process; a hardware switch cuts the mic line. `source_name="keyboard_fallback"`
  so the UI can badge it as such.
- **`software_mute_switch.py` — `SoftwareMuteSwitch`**: pure in-process
  state, driven only by API/UI calls — `source_name="software"`, same
  weaker-trust badging intent. The no-hardware demo/dev path.
- **`gpio_indicator.py` — `GpioIndicator`** / **`laptop_indicator.py` —
  `BlinkStickIndicator`/`SoftwareIndicator`**: LED state mirrors mute
  state, driven *only* by `CaptureGate` reacting to a `CaptureEvent` —
  never by UI state directly (an explicit architectural decision recorded
  in the docstrings).
- **`char_display.py` — `CharDisplay`**: 16x4 I2C character LCD via
  `RPLCD` — the one visual status output on a headless Pi build (no
  Chromium kiosk).

## `tpa/inference/`

- **`ollama_client.py` — `OllamaClient`**: talks to a local Ollama server
  over HTTP (`/api/chat`, streaming and non-streaming). Deliberately tuned
  after real benchmarking: `num_predict` is sent explicitly (previously
  defaulted to Ollama's own unbounded default — a 0.5B model once answered
  "Say hello" with 225 tokens of rambling); `think=False` disables
  reasoning-model "thinking" blocks (measured 16x speedup on Qwen3-8B for
  identical visible output). `verify_ready()`/`warmup()` are boot-time
  helpers called from `server.py`.
- **`llama_cpp_client.py` — `LlamaCppClient`**: loads a local GGUF file
  directly via `llama-cpp-python`, no server process. Fails loudly at
  construction if the model file is missing.
- **`factory.py` — `build_inference_client(backend, ...)`**: the single
  switchboard between the two — `backend="ollama"` or `"llama_cpp"` only;
  raises `UnsupportedBackendError` for any cloud backend name
  (`claude`/`copilot`/`hybrid_cloud`), enforcing REQ-M-02/M-03 at the
  composition boundary.
- **`gguf_model_manager.py` — `require_model(path, label="model")`**:
  centralizes the "fail loudly if the model file is missing, don't
  auto-download" check so `server.py` can validate all configured model
  paths up front at boot, rather than each adapter failing independently
  on first use.
- **Constrained decoding**: both clients accept `json_schema` and `temperature`
  on `complete()`. `LlamaCppClient` passes the schema as a llama.cpp grammar
  (`response_format`), so output is guaranteed valid JSON for that schema;
  `OllamaClient` uses Ollama's `format`. `LlamaCppClient.count_tokens(text)`
  gives exact token counts with the model's own tokenizer (prompt budgets).
- **`embedding_adapter.py` — `OnnxEmbeddingProvider`**: runs the staged
  `BAAI/bge-small-en-v1.5` ONNX model directly with `onnxruntime` + `tokenizers`
  (the embedding is the `[CLS]` hidden state, L2-normalised; 384 dimensions;
  `model_id` is the stable model name, not a path). It needs
  `data/bge-small-en-v1.5/model.onnx` and `tokenizer.json` and **never downloads
  at runtime**; a missing model raises `FileNotFoundError` naming the files and
  `scripts/setup_pi.sh`, which `_build_embedding` turns into "semantic memory OFF"
  rather than a crash. It replaced a `fastembed`-based adapter: fastembed passed a
  folder path where it needs a model name (so a staged model still failed), and its
  extra native packages (`mmh3`, `py-rust-stemmers`) are blocked by Windows
  Application Control on some machines and are not needed for dense embeddings.

## `tpa/notifications/`

- **`desktop_notifier.py` — `DesktopNotifier`**: Windows toast
  notifications via a PowerShell subprocess (`BalloonTip`).
- **`sound_player.py` — `SoundPlayer`**: Windows system sound alerts via
  `winsound`, tiered by `Urgency` (falls back through a list of candidate
  system `.wav` files, then to `MessageBeep` if none exist).

## `tpa/online/`

- **`allowlist.py`**: `DEFAULT_ALLOW_LIST` — the single source of truth for
  which hosts are permitted for outbound egress, read by both
  `AllowListedHttpClient` (transport-layer enforcement) and
  `service/lookup/registry.py`'s `FactProviderRegistry` (boot-time
  provider validation) — so the two can never disagree about what's
  permitted.
- **`http_client.py` — `AllowListedHttpClient`**: the *only* module in the
  codebase allowed to make outbound internet HTTP calls (`get`, `post_json`).
  Enforces `domain.policies.egress_policy.is_allowed()` on every request before
  it leaves the process; raises `EgressDeniedError` otherwise. Does **not**
  follow redirects (a redirect could leave the allow-list). Upstream failures
  (HTTP errors, timeouts, connection errors) are normalised to
  `ToolUnavailableError(status=...)`. Every request logs
  `[http] category= host= status= ms=` — never headers or bodies (they can
  carry API keys and user text).
- **`providers/`** — all implement `FactProviderPort`; structured results go
  in `FactAnswer.data`, a compact fact-only string in `.text`:
  - `weather.py` — `WeatherProvider`: Open-Meteo current conditions for a lat/lon
    (temperature, feels-like, humidity, wind, WMO condition, local observation time).
  - `geocode.py` — `GeocodingProvider` (category `geocode`): place name → ranked
    candidates via Open-Meteo's geocoder.
  - `fx.py` — `FxProvider`: Frankfurter (ECB, ~30 currencies) first; falls back to
    `open.er-api.com` (166 currencies) when Frankfurter 404s or is down.
    The converted figure is computed from the provider's rate, never by the model.
  - `tavily.py` — `TavilyProvider` (category `search`): real web search. Key read
    at call time from an injected callable (env var named by
    `privacy.online.search_api_key_env`), never logged; a missing key raises
    `ToolUnavailableError(not_configured=True)` without any network call.
    Only the model-chosen query leaves the device.
  The DuckDuckGo Instant Answer provider was removed — it only resolved
  encyclopedic entities and returned nothing for news or "latest" questions.
  Adding a fact category means writing one more file shaped like these.

## `tpa/stt/` and `tpa/tts/`

- **`faster_whisper.py` — `FasterWhisperProvider`**: requires the model
  already present in a local cache dir — raises clearly instead of
  silently downloading (a deliberate behavior change from the donor).
  Converts a domain `AudioWindow` (PCM bytes) to the float32-normalized
  samples the model wants internally, so callers never need to know that
  detail.
- **`piper.py` — `PiperProvider`** (the default TTS): local neural voice
  via Piper's `.onnx` models — no donor equivalent; added per the privacy
  roadmap to replace the donor's `edge-tts` (a network call to Microsoft's
  Bing Speech backend, which would violate REQ-M-06).
- **`piper.py` — `PiperProvider`**: reads the voice's own sample rate from `<voice>.onnx.json` (16000 for "low" voices, 22050 for "medium"), because playing at the wrong rate changes pitch and speed; falls back to 22050 if there is no config file. Needs a staged `.onnx` voice; nothing is downloaded.
- **`pyttsx3_provider.py` — `Pyttsx3Provider`**: the offline SAPI/eSpeak
  fallback, ported from the donor's `_synth_pyttsx3_sync` — the only TTS
  path actually carried over from the donor (the other half, `_synth_edge`,
  was the network-dependent one and was dropped entirely).

## `tpa/system/`

- **`windows_system_adapter.py` — `WindowsSystemAdapter`**: `psutil` +
  `pycaw` (volume) + `pywin32` (window focus) — all `pywinauto`/`pycaw`/
  `win32*` imports are lazy (inside methods, not module top) so the file
  stays importable on non-Windows dev machines. Maintains an app-name alias
  table (`"vs code"` → `Code.exe`, etc.) for natural-language app control.
- **`linux_system_adapter.py` — `LinuxSystemAdapter`**: no donor
  equivalent (the donor had no Linux path at all) — built to satisfy the
  identical `SystemControlPort` contract using `xdg-open`/`wmctrl` (app
  launch/focus) and `amixer` (volume), with `psutil` doing the
  process-management half identically on both platforms. Narrower by
  necessity: `focus_app` degrades to "already running" rather than failing,
  since window focus depends on a desktop environment that may not exist
  on a headless Pi.

## `tpa/speaker/`

Multi-speaker voice recognition — added so the assistant can tell an
enrolled household member's voice apart from an unrecognized one,
sharpening "don't react to stray talk" into "don't react to a voice
nobody taught it." Off by default (`audio.speaker_id.enabled: false`);
purely additive until enabled and at least one speaker is enrolled via
the `/api/speakers/*` routes. Backend selected by `audio.speaker_id.backend`
— see `docs/voice/open-source-wake-speaker-design.md` for why Resemblyzer
replaced Eagle as the default (no API key, fully offline).

- **`resemblyzer_speaker_recognizer.py` — `ResemblyzerSpeakerRecognizer`**
  (default backend): implements `SpeakerRecognitionPort` via
  `resemblyzer.VoiceEncoder`. Unlike Eagle, Resemblyzer has no per-frame
  scoring API at all — `embed_utterance()` needs a real chunk of audio
  (recommended ≥1.5–2s) to produce a stable embedding. The adapter
  buffers raw PCM into a rolling window (`speaker_id.score_window_sec`)
  and only recomputes cosine-similarity scores against each enrolled
  profile once the window fills; between recomputes it returns the last
  cached scores. `VoiceSession` already accumulates per-call scores into
  a running mean and decides once, so an answer that updates every N
  calls instead of every call needed no caller-side change.
- **`resemblyzer_speaker_enroller.py` — `ResemblyzerSpeakerEnroller`**
  (default backend): implements `SpeakerEnrollmentPort`. Collects
  `speaker_id.min_enroll_seconds` of audio, splits it into sub-utterances,
  embeds each, and persists the averaged, re-normalized embedding —
  standard d-vector enrollment practice, more robust than one long
  embedding against a single noisy stretch of audio.
- **`resemblyzer_profile_store.py` — `ResemblyzerProfileStore`**: persists
  one file per speaker at `data/speaker_profiles/<name>.resemblyzer.npy`
  (a plain 256-float32 vector via `numpy.save`). Deliberately distinct
  extension from Eagle's `.eagle` files so both backends can share the
  same `profiles_dir` without colliding — flipping `backend` back and
  forth never requires migrating profiles.
- **`eagle_speaker_recognizer.py` / `eagle_speaker_enroller.py` /
  `profile_store.py`** (`EagleSpeakerRecognizer`/`EagleSpeakerEnroller`/
  `EagleProfileStore`, Picovoice `pveagle`, opt-in via
  `speaker_id.backend: eagle`): unchanged from the original design —
  Eagle's real API is profile-agnostic at construction
  (`pveagle.create_recognizer()` takes no profiles; they're passed on
  every `process(pcm, speaker_profiles)` call instead), the enroller
  wraps a single `EagleProfiler` reset between sessions, and the store is
  a directory of opaque exported-profile blobs. Kept in the codebase for
  a possible future switch-back; needs `PICOVOICE_ACCESS_KEY` + `pveagle`
  installed, neither of which is required by the default backend above.

Every adapter here degrades to "feature disabled," never crashes boot,
on any construction failure — same shape as `_build_wake_word`.

## `tpa/wake_word/`

- **`openwakeword_engine.py` — `OpenWakeWordEngine`** (default engine):
  wraps `openwakeword.Model.predict()`. openWakeWord is frame-driven like
  Porcupine (not windowed like Resemblyzer), so the adapter buffers raw
  frames up to the engine's preferred 80ms chunk size and calls
  `predict()` once per full chunk, carrying any remainder to the next
  call. The configured phrase, "Hey Veda," is a custom-trained model, not
  one of openWakeWord's bundled pretrained words — see
  `docs/voice/open-source-wake-speaker-design.md` §4.8 for the
  synthetic-data training pipeline that produces it.
- **`porcupine.py` — `PorcupineWakeWord`** (Picovoice, opt-in via
  `wake_engine: porcupine`): wraps `pvporcupine`'s `process(pcm) -> int`
  (keyword index, `-1` if none) into the port's `bool` contract. Kept in
  the codebase for a possible future switch-back; needs
  `PICOVOICE_ACCESS_KEY` + `pvporcupine` installed, neither required by
  the default engine above.
- **`hotkey.py` — `HotkeyWakeWord`** (fallback): push-to-talk when no
  wake-word engine/keyword file is configured. `process()` always returns
  `False` (a hotkey isn't frame-driven) — callers check `is_pressed()`
  alongside the normal per-frame flow. Needs `pynput`'s global OS keyboard
  hook, which does not work on a headless Raspberry Pi (no X server) —
  this is why `openwakeword`, not `hotkey`, is the project's default.
