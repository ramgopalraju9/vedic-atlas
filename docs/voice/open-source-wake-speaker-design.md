# Open-Source Wake-Word & Speaker-ID — Design Document

**Status**: Proposed — not implemented. Nothing in this document has been
written to code yet; this is the design to review before any file is
created or edited.

## 1. Context

The project currently has two voice features built against Picovoice's
commercial SDKs:

- **Wake word** (`audio.wake_engine: porcupine`) — `tpa/wake_word/porcupine.py`,
  implementing `WakeWordPort`.
- **Multi-speaker recognition** (`audio.speaker_id`) —
  `tpa/speaker/eagle_speaker_recognizer.py` / `eagle_speaker_enroller.py`,
  implementing `SpeakerRecognitionPort` / `SpeakerEnrollmentPort`
  (see `docs/voice/speaker-recognition-design.md` for that original design).

Both need a `PICOVOICE_ACCESS_KEY` — a per-account API key issued by
Picovoice. The user has decided **not to depend on Picovoice at all**:
no account, no key, nothing that requires registering with a third party
to get the core voice features working. At the same time, neither
existing adapter should be deleted — they're correctly built, cost
nothing to leave in place, and the decision to avoid Picovoice today
doesn't have to be permanent. The ask is specifically:

> disable the configuration of Picovoice, keep that adapter as-is, and
> design the open-source replacement so it can be plugged in now while
> Picovoice stays pluggable for later.

This document designs that replacement using two API-key-free, fully
local, open-source libraries:

- **[openWakeWord](https://github.com/dscripka/openWakeWord)** (Apache-2.0)
  for wake-word detection, replacing Porcupine.
- **[Resemblyzer](https://github.com/resemble-ai/Resemblyzer)** (Apache-2.0)
  for speaker identification, replacing Eagle.

**Decided wake phrase: "Hey Veda."** openWakeWord's bundled pretrained
models only cover a fixed set of words it ships out of the box
(`hey_jarvis`, `alexa`, `hey_mycroft`, `hey_rhasspy`, `timer`, `weather`)
— there is no pretrained "Hey Veda" model to simply select by name.
Getting this specific phrase working means **training a small custom
classifier** on top of openWakeWord's shared embedding model, a one-time
offline step done on the dev machine, not on the Pi. §4.8 designs that
training pipeline. This is the one place in this document where
"install a package and point a config field at it" isn't the whole
story — flagged clearly rather than glossed over.

## 2. Goals / non-goals

**Goals**

- Wake word and speaker ID both work with zero external accounts, zero
  API keys, and zero network calls at runtime (consistent with the
  project's existing REQ-M-02 "no runtime auto-download when offline" rule
  — model files are staged once, manually, same as the Whisper STT model
  and the Qwen3 GGUF file already are).
- Reuse the existing `WakeWordPort`, `SpeakerRecognitionPort`, and
  `SpeakerEnrollmentPort` Protocols **unchanged**. This is a pure
  adapter-layer swap — if the ports needed to change, that would mean the
  original ISP boundary was drawn in the wrong place. It wasn't: Eagle's
  "frame in, per-speaker scores out" shape and Porcupine's "frame in,
  bool out" shape are generic enough that a totally different vendor's
  SDK fits them with no modification.
- The two Picovoice adapters stay in the codebase, stay wired into
  `server.py`'s builders, and stay selectable by config — just not the
  default, and not installed by default (their `pvporcupine`/`pveagle`
  dependencies move to clearly optional, opt-in dependency groups, as
  they already are).
- Everything degrades the same way everything else in this codebase
  does: a missing package, a missing model file, or a construction
  failure logs a warning and returns `None` — it never crashes boot.

**Non-goals**

- Not tuning detection thresholds against real recorded speech — that
  needs a live microphone and a human in the loop, same caveat as the
  original Eagle design doc's "known limitations" section.
- Not removing or modifying `tpa/wake_word/porcupine.py` or
  `tpa/speaker/eagle_*.py` in any way.
- Not fine-tuning the custom "Hey Veda" model against real recorded
  speech in this pass — §4.8 covers the synthetic-data bootstrap only;
  a later accuracy pass against real recordings is a separate, later
  decision once live hardware is reachable.

## 3. Integration with the current system

### 3.1 Where this sits in the architecture

No change to the diagram from the original speaker-recognition design —
this slots into the exact same seam:

```
mic ──frames──> CaptureGate (muted?) ──> VAD ──> UtteranceCollector
                     │
                     ├──> WakeWordPort.process(frame) -> bool
                     │     (PorcupineWakeWord | HotkeyWakeWord | OpenWakeWordEngine)
                     │
                     └──> SpeakerRecognitionPort.process(frame) -> list[float]
                           (EagleSpeakerRecognizer | ResemblyzerSpeakerRecognizer)
```

`VoiceSession` (`service/voice/voice_session.py`) never imports a vendor
SDK and never changes — it only ever talks to the Protocol. That's the
whole point of having drawn the port there in the first place, and this
design is the proof: a wake-word engine and a speaker-ID engine from a
completely different vendor family plug in without touching
`voice_session.py` at all.

### 3.2 Why this needs no new ports

Both new adapters implement the Protocols exactly as already defined —
no changes to either file:

```python
# domain/ports/wake_word_port.py — unchanged
@runtime_checkable
class WakeWordPort(Protocol):
    @property
    def frame_length(self) -> int: ...
    @property
    def sample_rate(self) -> int: ...
    def process(self, frame: bytes) -> bool: ...
```

```python
# domain/ports/speaker_recognition_port.py — unchanged
@runtime_checkable
class SpeakerRecognitionPort(Protocol):
    @property
    def frame_length(self) -> int: ...
    @property
    def sample_rate(self) -> int: ...
    @property
    def speaker_names(self) -> list[str]: ...
    def process(self, frame: bytes) -> list[float]: ...
    def reload_profiles(self) -> None: ...
```

```python
# domain/ports/speaker_enrollment_port.py — unchanged
@runtime_checkable
class SpeakerEnrollmentPort(Protocol):
    @property
    def frame_length(self) -> int: ...
    @property
    def sample_rate(self) -> int: ...
    def enroll_feed(self, frame: bytes) -> tuple[float, str]: ...
    def enroll_reset(self) -> None: ...
    def enroll_finish(self, speaker_name: str) -> None: ...
    def list_enrolled(self) -> list[str]: ...
    def delete_enrolled(self, speaker_name: str) -> bool: ...
```

### 3.3 A real behavioral difference worth naming up front: streaming vs. windowed

Porcupine and Eagle are both natively **frame-driven** — call `process()`
once per small audio frame (tens of milliseconds) and get an immediate
per-frame answer. openWakeWord and Resemblyzer are different in a way
that matters for how the adapter is built, not for the port contract:

- **openWakeWord** is still effectively frame-driven (it maintains its
  own internal feature buffer across calls and expects chunks around
  80ms), so `OpenWakeWordEngine.process()` can call the model on close to
  every invocation, matching Porcupine's immediacy.
- **Resemblyzer** has no per-frame API at all — `VoiceEncoder.embed_utterance()`
  takes a chunk of audio (recommended at least ~1.5–2 seconds for a
  stable embedding) and returns one embedding for that whole chunk.
  There is no way to get a meaningful score from a single 30ms frame.

This is handled entirely inside the adapter, invisibly to
`SpeakerRecognitionPort`'s contract: `ResemblyzerSpeakerRecognizer.process(frame)`
appends the frame to an internal rolling buffer and returns the
**last computed** score list on every call; only when the buffer has
accumulated `score_window_sec` worth of audio does it actually run the
encoder and refresh the cached scores. `VoiceSession` already expects
exactly this shape — it accumulates per-call scores into a running mean
over the whole armed window and decides once (`_resolve_speaker()`), not
per-frame — so a port implementation that updates its answer every N
calls instead of every call is already compatible with how the caller
uses it. No change needed in `voice_session.py`.

### 3.4 Composition root wiring (server.py) — design, not yet written

`_build_wake_word()` gains one more branch, selected the same way the
existing `hotkey`/`porcupine` branches are — by `cfg.audio.wake_engine`:

```python
if engine == "openwakeword":
    # "Hey Veda" is a custom-trained model (§4.8), not one of openwakeword's
    # bundled pretrained words — model_path is required for this project,
    # unlike a pretrained-name selection where it could be left None.
    model_path = cfg.audio.wake_oww_model_path
    if not model_path:
        logger.warning("[voice] openwakeword needs audio.wake_oww_model_path (custom 'Hey Veda' model); wake disabled")
        return None
    try:
        from core.constants import PROJECT_ROOT
        from tpa.wake_word.openwakeword_engine import OpenWakeWordEngine
        resolved = model_path if Path(model_path).is_absolute() else PROJECT_ROOT / model_path
        wake = OpenWakeWordEngine(
            model_name=cfg.audio.wake_oww_model,
            model_path=str(resolved),
            threshold=cfg.audio.wake_threshold,
        )
        logger.info(f"[voice] wake engine 'openwakeword' active ({cfg.audio.wake_oww_model})")
        return wake
    except Exception as e:
        logger.warning(f"[voice] openwakeword unavailable ({e}); wake disabled")
        return None
```

`_build_speaker_id()` and `_build_speaker_enrollment_service()` branch on
a new `cfg.audio.speaker_id.backend` field instead of assuming Eagle:

```python
def _build_speaker_id(cfg: AppConfig):
    scfg = cfg.audio.speaker_id
    if not scfg.enabled:
        return None
    backend = (scfg.backend or "resemblyzer").lower()
    try:
        from core.constants import PROJECT_ROOT
        profiles_dir = PROJECT_ROOT / scfg.profiles_dir
        if backend == "resemblyzer":
            from tpa.speaker.resemblyzer_speaker_recognizer import ResemblyzerSpeakerRecognizer
            recognizer = ResemblyzerSpeakerRecognizer(
                profiles_dir=profiles_dir,
                score_window_sec=scfg.score_window_sec,
            )
        elif backend == "eagle":
            access_key = _picovoice_access_key()
            if not access_key:
                logger.warning("[voice] speaker ID backend 'eagle' needs PICOVOICE_ACCESS_KEY; disabled")
                return None
            from tpa.speaker.eagle_speaker_recognizer import EagleSpeakerRecognizer
            recognizer = EagleSpeakerRecognizer(access_key=access_key, profiles_dir=profiles_dir)
        else:
            logger.warning(f"[voice] unknown speaker_id backend '{backend}'; disabled")
            return None
        logger.info(f"[voice] speaker ID active, backend={backend} ({len(recognizer.speaker_names)} enrolled)")
        return recognizer
    except Exception as e:
        logger.warning(f"[voice] speaker ID unavailable ({e}); disabled")
        return None
```

`_build_speaker_enrollment_service()` mirrors the same `backend` branch,
constructing `ResemblyzerSpeakerEnroller` or `EagleSpeakerEnroller`
accordingly. `_picovoice_access_key()` is untouched — it's only ever
called from the `eagle`/`porcupine` branches now, never on the default
path.

This is the concrete form of "disable Picovoice's configuration, keep the
adapter": nothing about `porcupine.py` or `eagle_*.py` changes; they
simply stop being what `backend`/`wake_engine` default to, and their
dependency groups stop being installed by default. Setting
`wake_engine: porcupine` or `speaker_id.backend: eagle` (plus the access
key and `pvporcupine`/`pveagle` installed) brings them straight back —
that's the entire "plug it back in later" story, and it requires editing
one YAML value, not code.

## 4. New components, in detail

### 4.1 `OpenWakeWordEngine` (`tpa/wake_word/openwakeword_engine.py`)

Implements `WakeWordPort`. Wraps `openwakeword.Model`.

```python
class OpenWakeWordEngine:
    def __init__(self, model_name: str = "hey_veda", model_path: str | None = None,
                 threshold: float = 0.5, chunk_samples: int = 1280, sample_rate: int = 16000):
        from openwakeword.model import Model
        # model_path is required in practice for this project: "Hey Veda" is a
        # custom-trained model (§4.8), not one of openwakeword's bundled
        # pretrained words, so there is no None-means-"use a bundled name"
        # fallback worth relying on here (the adapter still accepts None
        # structurally, in case a bundled pretrained word is ever used instead).
        paths = [model_path] if model_path else None
        if model_path:
            from pathlib import Path
            if not Path(model_path).exists():
                raise FileNotFoundError(f"openwakeword model not found at {model_path}")
        self._model = Model(wakeword_models=paths, inference_framework="onnx")
        self._model_name = model_name
        self._threshold = threshold
        self._chunk_samples = chunk_samples  # 1280 = 80ms @ 16kHz, openwakeword's own recommendation
        self._sample_rate = sample_rate
        self._buf = bytearray()

    @property
    def frame_length(self) -> int:
        return self._chunk_samples

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def process(self, frame: bytes) -> bool:
        import numpy as np
        self._buf += frame
        chunk_bytes = self._chunk_samples * 2  # int16 -> 2 bytes/sample
        if len(self._buf) < chunk_bytes:
            return False
        chunk, self._buf = bytes(self._buf[:chunk_bytes]), self._buf[chunk_bytes:]
        pcm = np.frombuffer(chunk, dtype=np.int16)
        scores = self._model.predict(pcm)
        return scores.get(self._model_name, 0.0) >= self._threshold

    def start(self) -> None:
        pass  # frame-driven, no background thread — matches PorcupineWakeWord's own no-op

    def stop(self) -> None:
        pass  # Model has no explicit teardown
```

Same slicing idiom `_wake_triggered()` in `voice_session.py` already uses
for Porcupine (buffer bytes until there's enough for one native call,
carry the remainder) — no change needed there, since `frame_length`
still reports whatever chunk size the engine wants and the existing
caller code already slices to it generically.

### 4.2 `ResemblyzerSpeakerRecognizer` (`tpa/speaker/resemblyzer_speaker_recognizer.py`)

Implements `SpeakerRecognitionPort`. Wraps `resemblyzer.VoiceEncoder`.

```python
class ResemblyzerSpeakerRecognizer:
    def __init__(self, profiles_dir: Path, score_window_sec: float = 2.0, sample_rate: int = 16000):
        from resemblyzer import VoiceEncoder
        self._encoder = VoiceEncoder("cpu")
        self._store = ResemblyzerProfileStore(profiles_dir)
        self._profiles: dict[str, np.ndarray] = self._store.load_all()
        self._sample_rate = sample_rate
        self._window_samples = int(score_window_sec * sample_rate)
        self._buf = bytearray()
        self._last_scores: list[float] = [0.0] * len(self._profiles)

    @property
    def frame_length(self) -> int:
        return 0  # not frame-size-sensitive; accepts any chunk, same pattern as HotkeyWakeWord reporting 0

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def speaker_names(self) -> list[str]:
        return list(self._profiles.keys())

    def process(self, frame: bytes) -> list[float]:
        if not self._profiles:
            return []
        self._buf += frame
        window_bytes = self._window_samples * 2
        if len(self._buf) < window_bytes:
            return self._last_scores
        window, self._buf = bytes(self._buf[-window_bytes:]), bytearray()
        pcm = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
        embedding = self._encoder.embed_utterance(pcm)
        self._last_scores = [
            float(np.dot(embedding, profile))  # both are unit-normalized -> dot product == cosine similarity
            for profile in self._profiles.values()
        ]
        return self._last_scores

    def reload_profiles(self) -> None:
        self._profiles = self._store.load_all()
        self._last_scores = [0.0] * len(self._profiles)
```

### 4.3 `ResemblyzerSpeakerEnroller` (`tpa/speaker/resemblyzer_speaker_enroller.py`)

Implements `SpeakerEnrollmentPort`. Collects a configurable amount of
speech, splits it into sub-utterances, embeds each, and persists the
**averaged, re-normalized** embedding as the enrolled profile — the
standard practice for d-vector-style enrollment, and more robust than a
single long embedding against momentary noise.

```python
class ResemblyzerSpeakerEnroller:
    def __init__(self, profiles_dir: Path, min_enroll_seconds: float = 12.0,
                 sub_utterance_sec: float = 3.0, sample_rate: int = 16000):
        from resemblyzer import VoiceEncoder
        self._encoder = VoiceEncoder("cpu")
        self._store = ResemblyzerProfileStore(profiles_dir)
        self._sample_rate = sample_rate
        self._min_samples = int(min_enroll_seconds * sample_rate)
        self._sub_samples = int(sub_utterance_sec * sample_rate)
        self._buf = bytearray()

    @property
    def frame_length(self) -> int:
        return 0

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def enroll_feed(self, frame: bytes) -> tuple[float, str]:
        self._buf += frame
        collected_samples = len(self._buf) // 2
        pct = min(100.0, 100.0 * collected_samples / self._min_samples)
        feedback = (
            "keep talking" if pct < 100.0 else
            "enough audio collected — call enroll_finish to save"
        )
        return pct, feedback

    def enroll_reset(self) -> None:
        self._buf = bytearray()

    def enroll_finish(self, speaker_name: str) -> None:
        collected_samples = len(self._buf) // 2
        if collected_samples < self._min_samples:
            # Plain exception, not AppException — tpa/ adapters never import
            # exceptions/core.enums in this codebase (same as EagleSpeakerEnroller).
            # service/speakers/speaker_enrollment_service.py's finish() already
            # catches any exception from enroll_finish() and wraps it into
            # AppException(VALIDATION_ERROR) itself — see SpeakerEnrollmentService.finish()
            # lines 81-90, unchanged by this design. Raising AppException here directly
            # would duplicate that translation at the wrong layer.
            raise ValueError(
                f"enrollment needs at least {self._min_samples / self._sample_rate:.0f}s of audio, "
                f"got {collected_samples / self._sample_rate:.1f}s"
            )
        pcm = np.frombuffer(bytes(self._buf), dtype=np.int16).astype(np.float32) / 32768.0
        sub_embeds = [
            self._encoder.embed_utterance(pcm[i:i + self._sub_samples])
            for i in range(0, len(pcm) - self._sub_samples + 1, self._sub_samples)
        ]
        averaged = np.mean(sub_embeds, axis=0)
        averaged /= np.linalg.norm(averaged)
        self._store.save(speaker_name, averaged)
        self._buf = bytearray()

    def list_enrolled(self) -> list[str]:
        return self._store.list_names()

    def delete_enrolled(self, speaker_name: str) -> bool:
        return self._store.delete(speaker_name)
```

The `ValueError` above surfaces as `ExceptionCode.VALIDATION_ERROR` with
no code changes needed: `SpeakerEnrollmentService.finish()`
(`service/speakers/speaker_enrollment_service.py:81-90`, unchanged by
this design) already wraps *any* exception from `enroll_finish()` into
`AppException(..., ExceptionCode.VALIDATION_ERROR, ...)` — it's the exact
same path the Eagle enroller's "finished before 100%" case already goes
through today. `tpa/speaker/resemblyzer_speaker_enroller.py` itself never
imports `AppException` or `ExceptionCode`, matching every other adapter
in `tpa/` (e.g. `LlamaCppClient` raises a plain `FileNotFoundError` for
its missing-model case, not an `AppException`) — translation into the
app's exception model is strictly a service/controller-layer
responsibility in this codebase, never an adapter one.

### 4.4 `ResemblyzerProfileStore` (`tpa/speaker/resemblyzer_profile_store.py`)

Pure bytes-in/bytes-out persistence, parallel to the existing
`EagleProfileStore`, but storing a plain 256-float32 vector per speaker
instead of an opaque vendor-exported blob — genuinely simpler, since
there's no vendor serialization format to round-trip through:

```python
class ResemblyzerProfileStore:
    _EXT = ".resemblyzer.npy"

    def __init__(self, profiles_dir: Path):
        self._dir = profiles_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, embedding: np.ndarray) -> None:
        np.save(self._dir / f"{name}{self._EXT}", embedding)

    def load_all(self) -> dict[str, np.ndarray]:
        return {
            p.name.removesuffix(self._EXT): np.load(p)
            for p in sorted(self._dir.glob(f"*{self._EXT}"))
        }

    def list_names(self) -> list[str]:
        return [p.name.removesuffix(self._EXT) for p in sorted(self._dir.glob(f"*{self._EXT}"))]

    def delete(self, name: str) -> bool:
        path = self._dir / f"{name}{self._EXT}"
        if not path.exists():
            return False
        path.unlink()
        return True
```

The `.resemblyzer.npy` extension is deliberately distinct from Eagle's
`.eagle` extension. Both backends can point `profiles_dir` at the same
directory (`data/speaker_profiles`, the existing default) without ever
colliding or cross-loading each other's files — switching
`speaker_id.backend` back and forth doesn't require moving or deleting
anything.

### 4.5 Config changes

`AudioConfig` (`src/schemas/config_schemas.py`) — additive fields only,
every existing field untouched:

```python
class AudioConfig(BaseModel):
    ...
    wake_engine: str = "openwakeword"  # none | hotkey | openwakeword | porcupine
    ...
    wake_oww_model: str = "hey_veda"          # the model's registered name (predict() score dict key)
    wake_oww_model_path: str = "data/models/openwakeword/hey_veda.onnx"  # custom-trained, see §4.8 — required, no bundled fallback for this phrase
    wake_threshold: float = 0.5
    ...
    speaker_id: SpeakerIdConfig = SpeakerIdConfig()
```

```python
class SpeakerIdConfig(BaseModel):
    enabled: bool = False
    backend: str = "resemblyzer"       # resemblyzer | eagle
    profiles_dir: str = "data/speaker_profiles"
    match_threshold: float = 0.6       # ⚠ see §6 — re-tune when switching backend, scales differ
    require_known_speaker: bool = false
    min_enroll_seconds: float = 12.0   # resemblyzer only
    score_window_sec: float = 2.0      # resemblyzer only
```

`config/audio.yaml` — the default changes from `wake_engine: hotkey` to
`wake_engine: openwakeword` (making the key-free, headless-capable engine
the actual default, not just an available option — this also happens to
fix the separate headless-Pi hotkey problem already on record in
`docs/07-backlog.md`, since openWakeWord is frame-driven like Porcupine
and needs no OS keyboard hook at all):

```yaml
wake_word_enabled: true
# Wake trigger: none (transcribe while unmuted) | hotkey (press a key; needs
# an interactive desktop session, does not work headless) | openwakeword
# (spoken wake word, local ONNX model, no key, works headless — default) |
# porcupine (spoken wake word via Picovoice; needs PICOVOICE_ACCESS_KEY + .ppn,
# kept available but not installed/used by default).
wake_engine: openwakeword
# "Hey Veda" — custom-trained model (§4.8), not a bundled pretrained word.
# Must be staged at this path before wake_engine=openwakeword will work;
# _build_wake_word() disables the wake engine (logs a warning, degrades to
# None) if the file is missing, same as every other adapter in this project.
wake_oww_model: hey_veda
wake_oww_model_path: data/models/openwakeword/hey_veda.onnx
wake_threshold: 0.5
wake_hotkey: <f9>
wake_window_sec: 8.0
wake_keyword_path: null
...

# Multi-speaker voice recognition. backend: resemblyzer (local, no key,
# default) | eagle (Picovoice, needs PICOVOICE_ACCESS_KEY, kept available
# but not installed/used by default). Off by default and purely additive;
# enroll speakers via /api/speakers/enroll/* before turning on
# require_known_speaker, or the assistant will respond to no one.
speaker_id:
  enabled: false
  backend: resemblyzer
  profiles_dir: data/speaker_profiles
  match_threshold: 0.6
  require_known_speaker: false
  min_enroll_seconds: 12.0
  score_window_sec: 2.0
```

### 4.6 Dependencies (`pyproject.toml`) — new optional-dependency groups

Versions below are PyPI's current latest at time of writing, checked
directly rather than guessed (`pip index versions <pkg>`, 2026-10-03):
`openwakeword` 0.6.0, `onnxruntime` 1.30.0, `resemblyzer` 0.1.4, `torch` 2.14.1.
Pins use the same `>=` floor style as every other group in this file —
not exact-pinned, consistent with the existing convention.

```toml
# audio.wake_engine=openwakeword (tpa/wake_word/openwakeword_engine.py) —
# the default spoken-wake-word engine: local ONNX models, no API key,
# works headless (no X server / keyboard hook needed, unlike wake_engine=hotkey).
wake-openwakeword = [
    "openwakeword>=0.6.0",
    "onnxruntime>=1.17.0",
]

# audio.speaker_id.backend=resemblyzer (tpa/speaker/resemblyzer_*.py) — the
# default multi-speaker recognition backend: local PyTorch model (weights
# ship inside the pip package itself, no separate download), no API key.
# torch is a heavy dependency (several hundred MB) — see design doc
# docs/voice/open-source-wake-speaker-design.md §6 for the tradeoff vs. Eagle.
speaker-id-resemblyzer = [
    "resemblyzer>=0.1.4",
    "torch>=2.2.0",
]
```

The existing `wake-porcupine` and `speaker-id` (Eagle) groups are
untouched — they remain exactly as defined, still installable, still
real optional extras. Nothing about them changes; they're just no longer
what a fresh install pulls in by default (and never were — all of these
groups are opt-in extras already, per the existing project convention of
a minimal base `dependencies` list).

### 4.7 Model staging (manual, no runtime download — REQ-M-02)

- **openWakeWord / "Hey Veda"**: there is no pretrained file to download
  for this phrase — it has to be produced by the training pipeline in
  §4.8 below, then staged under `data/models/openwakeword/hey_veda.onnx`,
  same pattern as the Whisper STT model and the Qwen3 GGUF file
  (produced/downloaded once, manually, by whoever provisions the device,
  never fetched by the running server). `wake_oww_model_path` points at
  the staged file. If the project ever wants a *pretrained* word instead
  (`hey_jarvis`, `alexa`, etc.) that step is simpler — those ship as
  GitHub release assets in the openWakeWord repo and need no training —
  but that's not the phrase that was decided on here.
- **Resemblyzer**: no staging step at all — the pretrained encoder
  weights ship inside the `resemblyzer` pip package's own data files.
  Installing the package is the entire "staging" step. This is a real
  operational simplification over both Eagle (needs a key) and
  openWakeWord (needs a manually-staged — here, manually-*trained* —
  model file).

### 4.8 Training the custom "Hey Veda" model (one-time, dev-machine only)

openWakeWord's standard way to add a word it doesn't ship pretrained is
documented in its own repo
(`notebooks/automatic_model_training.ipynb` upstream) as a
**synthetic-data bootstrap**: generate many spoken variations of the
phrase with text-to-speech instead of recording a human saying it
hundreds of times, then train a small classifier on top of openWakeWord's
shared, already-trained audio-embedding model (that shared embedding
model is the expensive part and is reused as-is, unmodified — training
only adds a lightweight fully-connected head on top of it, which is why
this is feasible on a laptop CPU rather than needing a GPU cluster: the
installed `openwakeword.train.Model` class is a small FCN, not a deep
network).

This subsection was originally written before actually attempting the
pipeline. It's since been attempted for real, and three things turned
out different from the original plan — recorded here rather than
silently edited away, since they're exactly the kind of thing worth
knowing before attempting this again.

**Correction 1 — "reuse the project's Piper TTS engine" was half right.**
`piper-sample-generator` (the tool openWakeWord's own notebook actually
uses for bulk synthesis) is a *separate* GitHub repo from the `piper-tts`
pip package this project already uses for spoken replies, even though
both are built on the same underlying Piper voice models. Worse:
`piper-sample-generator` depends on `piper-phonemize`, a C++ extension
that — verified directly against its PyPI/GitHub-releases listing, not
assumed — **ships no Windows wheel at all, and its Linux wheels cap at
Python 3.11** (this project's venv is Python 3.12 on Windows,
incompatible on both axes). Same root-cause class of problem as the
`llama-cpp-python` build issue earlier this session: a native C++
extension with no wheel for this platform.

**The actual fix used**: skip `piper-sample-generator` entirely and
synthesize the "Hey Veda" clips with the modern `piper-tts` package
instead (already Windows-compatible, confirmed by installing it
directly — ships a `win_amd64` wheel). It exposes a clean Python API
(`piper.PiperVoice.load()` / `.synthesize()`) with a `SynthesisConfig`
that takes `speaker_id`, `length_scale`, `noise_scale`, `noise_w_scale` —
the exact variety knobs the original plan wanted. The same underlying
multi-speaker voice family `piper-sample-generator` uses internally
(`en_US-libritts_r-medium`) is independently published as a standard
ONNX Piper voice at
`https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx`
with **904 distinct speaker IDs in one 78.5MB file** — so the voice
variety is unchanged, only the tool invoking it changed.
`openwakeword.train`'s `__main__` block imports a module named
`generate_samples` unconditionally (even when only running
`--augment_clips`/`--train_model`, not `--generate_clips`) — handled
with a trivial no-op stub module at that import path, since we populate
the clip directories ourselves and never call the real function.

**Correction 2 — the official negative-feature file is 17.3GB, not
"several GB," and is being deferred.** `openwakeword_features_ACAV100M_2000_hrs_16bit.npy`
(2000 hours of diverse real-world audio — speech, music, ambient noise —
pre-extracted into feature embeddings, published so users don't have to
recompute features from raw audio themselves) is genuinely **17,280,000,128
bytes** by direct `HEAD` request, not a rough guess. Decision: train the
first "Hey Veda" model *without* it — using only the synthetic
TTS-generated adversarial negatives plus a smaller FMA-music background
set — to get a working model faster. The real cost of including it
isn't the download so much as a ~10x larger per-step training batch
(`batch_n_per_class: {ACAV100M_sample: 1024, adversarial_negative: 50,
positive: 50}` in the official template — removing that key drops the
batch from 1124 to 100 rows). The tradeoff, stated plainly: without it,
the model has only ever seen synthetic TTS negatives and ~1 hour of FMA
music as "not the wake word," so it's more likely to false-trigger on
real household sounds (TV audio, traffic, appliance noise) it was never
shown during training than the official full-data recipe would be. This
is a real quality tradeoff, not a free lunch — revisit once the lighter
model's false-positive rate is actually measured against real audio.
Download link, for whenever a from-the-full-recipe retrain is wanted:
`https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy`

**Why not just repurpose faster-whisper (already staged) instead of
training a separate wake-word model?** Asked directly mid-implementation,
worth recording since it's a reasonable question. The two models solve
structurally different problems:

- `VoiceSession._tick()` (confirmed by reading the actual tick loop, not
  assumed) calls STT **exactly once per detected utterance** — a few
  seconds of buffered audio, after VAD has already decided speech
  happened. It is never called per-frame.
- A wake-word model is built to run on literally every incoming audio
  frame (every 30-80ms), continuously, cheaply enough that doing so
  24/7 never matters for CPU budget — it's a tiny classifier (a few
  thousand parameters), not a transformer encoder/decoder.
- Running Whisper continuously instead of gating it behind a cheap
  classifier is exactly what `wake_engine: none` already does in this
  project (tested directly earlier this session) — it works, but means
  running the expensive STT model on *everything*, which is the
  computational cost (and, in spirit, the "reacts to all stray talk"
  privacy cost) the wake-word gate exists specifically to avoid.
  Whisper being already trained doesn't help here because it's trained
  for the wrong *shape* of job — open-vocabulary transcription of
  multi-second clips, not a continuous single-phrase frame classifier —
  not because of anything about its training data or quality.

**Pipeline, concretely (as actually run, no-ACAV variant):**

1. **Synthesize positive + adversarial-negative examples** using
   `piper-tts`'s Python API against the downloaded
   `en_US-libritts_r-medium.onnx` voice (904 speaker IDs), randomizing
   `speaker_id`/`length_scale`/`noise_scale` per sample for variety, into
   the `positive_train`/`positive_test`/`negative_train`/`negative_test`
   directories `openwakeword.train` expects.
2. **Background/RIR data**: ~1 hour of FMA music (streamed via the
   `datasets` library, matching openWakeWord's own notebook code) for
   `background_paths`, plus the MIT environmental impulse response
   dataset (`davidscripka/MIT_environmental_impulse_responses` on
   HuggingFace, also via `datasets` streaming) for `rir_paths`. No
   AudioSet clips this pass (its HF dataset layout has since changed
   from what the upstream notebook references, hit a 404 verifying it,
   not worth chasing for a first-pass model — FMA alone is enough to
   prove the pipeline).
3. **Augmentation**: synthetic clips mixed with the RIR/background data
   via `openwakeword.data.augment_clips` (unmodified, stock behavior).
4. **Train the classifier head**: `openwakeword.train` run with
   `--augment_clips --train_model` (not `--generate_clips`, since we
   populate the clip directories ourselves per Correction 1), using the
   `feature_data_files` dict *without* the `ACAV100M_sample` entry.
5. **Export to ONNX**: `oww.export_model(...)` (stock behavior, built
   into `train.py`) — produces `hey_veda.onnx`, staged to
   `data/models/openwakeword/hey_veda.onnx`. None of the training tooling
   (piper-tts used as synthesizer, the training scripts, the FMA/RIR raw
   audio) needs to exist on the Pi — only this one file does.

**New, training-only dependencies** (dev machine, never installed on the
Pi — training tooling is explicitly outside this project's runtime
footprint): `torchinfo`, `torchmetrics`, `datasets` (HuggingFace), plus
`piper-tts` (already in `tts-piper`, reused as the synthesizer per
Correction 1 — not `piper-sample-generator`/`piper-phonemize`, which this
environment can't install at all).

```toml
# One-time offline training of the custom "Hey Veda" wake-word model
# (docs/voice/open-source-wake-speaker-design.md §4.8). Dev-machine only —
# none of this needs to be installed on the Pi; only the exported .onnx
# file from the training run does. Deliberately NOT piper-sample-generator/
# piper-phonemize — that C++ extension ships no Windows wheel and its Linux
# wheels cap at Python 3.11 (confirmed, not assumed); piper-tts (already in
# tts-piper) is used as the synthesizer instead, see §4.8 Correction 1.
wake-openwakeword-training = [
    "openwakeword>=0.6.0",       # same package also used at runtime; its
                                   # training submodule is what's exercised here
    "piper-tts>=1.2.0",           # already in tts-piper; reused as the synthetic-
                                   # data voice generator, not a new dependency concept
    "torchinfo>=1.8.0",
    "torchmetrics>=1.0.0",
    "datasets>=2.0.0",            # streams the FMA/MIT-RIR background data
]
```

**Status**: done — a first `hey_veda.onnx` was actually trained
(2026-10-03) and is staged at `data/models/openwakeword/`. Real measured
numbers and every remaining fix needed to get there, below.

### 4.8.1 Correction 3 — six more Windows-only compatibility gaps, found by actually running the pipeline

The design above (Correction 1 and 2) anticipated the two big structural
problems. Actually running `openwakeword.train` on Windows surfaced six
more — all genuinely Windows-specific (the package is developed and
tested on Linux), none affecting the training *logic* or the resulting
model's correctness. Recorded here because the next person retraining
this model (more samples, the full ACAV100M set, a different phrase)
will hit every one of them again otherwise. All fixes live in
`wakeword_training/run_train.py`, a thin wrapper that applies them as
monkeypatches and then runs the unmodified `openwakeword/train.py` via
`runpy` — nothing in the installed `openwakeword` package itself was
edited.

1. **`acoustics` (an unconditional `openwakeword.data` import, for
   directivity math never actually exercised by this training config)
   imports `scipy.special.sph_harm`, removed in current scipy (1.18.1)
   in favor of `sph_harm_y` — a different argument order *and* a
   different polar/azimuthal convention. `acoustics` 0.2.6 (latest on
   PyPI) predates this scipy change and has no newer release. Fixed
   with a compatibility shim aliasing the old name to the new function
   with the arguments correctly remapped.
2. **`torchaudio` 2.11 (the only torchaudio release pip could resolve
   against our torch 2.14 — no matching torchaudio exists yet) hard-requires
   `torchcodec` for `torchaudio.load()`, with no working fallback once
   `torchcodec` is installed, and `torchcodec` itself needs a system
   FFmpeg install not present here** (confirmed: DLL load failure for
   every FFmpeg version 4 through 9 it tried). Since every file this
   pipeline loads is a plain 16-bit PCM WAV we generated ourselves,
   `torchaudio.load` was replaced with a `soundfile`-backed equivalent
   (already an installed dependency) rather than adding a new
   system-level FFmpeg dependency for this one call.
3. **`openwakeword.data.trim_mmap()` opens the source feature file via
   `np.load(path, mmap_mode='r')` and never releases that handle before
   calling `os.remove()` on the same path.** POSIX allows unlinking an
   open file (where this was developed); Windows does not — confirmed
   directly (`PermissionError: ... being used by another process`).
   Fixed with an explicit `del` + `gc.collect()` before the remove/rename,
   otherwise identical logic. The *caller*,
   `compute_features_from_generator()`, has the exact same problem one
   level up (it holds its own separate open memmap handle, `fp`, to the
   same file when it calls `trim_mmap`) — fixed the same way, as a
   second patched copy.
4. **`datasets` 4.x+ also requires `torchcodec`** for its own audio
   decoding (same root cause as #2, different caller — this one hit
   while streaming the MIT RIR / FMA background datasets, not the
   training script itself). Fixed by pinning `datasets<4.0`, the last
   major version that decodes audio via `soundfile` directly.
5. **`rudraml/fma` (the HF dataset openWakeWord's own notebook streams
   for background-music negatives) requires `trust_remote_code=True`** —
   executing third-party Python from that dataset's repo. Declined
   without explicit sign-off (a real security-relevant decision, not a
   compatibility shim) — this training run proceeded with the MIT RIR
   data only, no FMA background clips.
6. **PyTorch's `DataLoader` with `num_workers>0` spawns worker
   processes; on Windows that means the `spawn` start method (no
   `fork()`), which requires every argument reaching a worker to be
   picklable.** `openwakeword.train`'s own batch-generator closure isn't
   (confirmed: `PicklingError` on a lambda inside it, built/tested on
   Linux where `fork()` has no such restriction). Fixed by capping
   `os.cpu_count()` at 1 for this process, which makes train.py's own
   `n_cpus // 2` sizing resolve to 0 workers — the model is tiny (a
   small FCN), so losing worker-process parallelism for data loading
   isn't a meaningful speed cost. This in turn required a second small
   patch: `train.py` hardcodes `prefetch_factor=16` regardless of
   `num_workers`, which `torch.utils.data.DataLoader` rejects outright
   when `num_workers=0` — wrapped to drop that kwarg in that case.

Two more surfaced at the very end, past the training loop itself:

7. **ONNX export needs `onnxscript`**, not installed by `openwakeword`'s
   own declared dependencies. A plain missing-package fix.
8. **The exported `.onnx` file is accompanied by a separate
   `hey_veda.onnx.data` file** (PyTorch's newer ONNX exporter writes
   weights as external data rather than embedding them inline) — easy
   to miss when staging the model, since the small `.onnx` file alone
   *looks* complete. **Both files must be copied to
   `data/models/openwakeword/` together** — confirmed the hard way: the
   server booted and logged a clean ONNX Runtime error
   (`External data path does not exist: ...hey_veda.onnx.data`) when
   only the `.onnx` file was staged, degrading safely rather than
   crashing, exactly as designed, but silently producing a disabled wake
   engine until the second file was found and copied too.

### 4.8.2 Real measured numbers (first pass, no ACAV100M, 2026-10-03)

- **Synthetic clip generation** (piper-tts, 2000+400 positive + 4000+800
  adversarial-negative "hey veda"-adjacent clips): **~5.5 minutes**
  (~32-45ms/clip after a ~3s one-time model load).
- **Background/RIR data**: MIT RIR dataset (270 real impulse-response
  clips) streamed in **~4.5 minutes**; FMA skipped (§4.8.1 item 5).
- **`--augment_clips` phase** (augmentation + feature extraction for all
  four splits): **~2m14s** total for 7,200 clips.
- **`--train_model` phase** (10,000 steps + two 1,000-step auto-tuning
  passes, batch size 100 per step — no ACAV100M in the batch):
  **~5m30s**, confirming the earlier prediction that the model itself
  (a small FCN) is cheap to train; the batches were small enough that
  this never became I/O- or compute-bound.
- **Total, start to a working `.onnx` file**: well under 20 minutes of
  actual compute, spread across a much longer wall-clock session because
  of the eight compatibility fixes in between.

**Accuracy — the honest part.** Tested the real exported model through
the real `OpenWakeWordEngine` adapter (not just inside the training
script) against freshly synthesized clips the model never saw during
training:

| Test | Result |
|---|---|
| "hey veda", 4 new speaker IDs | **2/4 triggered** (speakers 300, 850; speakers 10, 600 did not) |
| 4 unrelated/adversarial phrases | **1/4 false-triggered** ("turn on the lights"; "what time is it" / "hey vera" / "hello there" correctly did not) |
| White noise | correctly did not trigger |

This is a real, working wake-word model — the mechanics are proven
end-to-end — but roughly 50% recall and a visible false-positive rate is
**not production quality**. This is exactly the tradeoff flagged back in
§4.8's original "Correction 2": skipping the 17.3GB ACAV100M negative
set and using only ~6,000 synthetic clips gets a fast first pass at a
real accuracy cost. The honest next step is a retrain with more
synthetic samples, the FMA background data (once the `trust_remote_code`
question is resolved one way or the other), and/or the full ACAV100M
set — the pipeline and every compatibility fix above already work, so
that retrain is a config-and-rerun away, not a new debugging session.

## 5. Picovoice adapters: explicitly preserved, not removed

To be unambiguous about what "disable the configuration" means here,
since it's easy to conflate with "remove":

| File | Status after this change |
|---|---|
| `tpa/wake_word/porcupine.py` | **Untouched.** Still implements `WakeWordPort`. Still constructed by `_build_wake_word()` when `wake_engine: porcupine` is set. |
| `tpa/speaker/eagle_speaker_recognizer.py` | **Untouched.** Still implements `SpeakerRecognitionPort`. Still constructed when `speaker_id.backend: eagle`. |
| `tpa/speaker/eagle_speaker_enroller.py` | **Untouched.** Same, for `SpeakerEnrollmentPort`. |
| `tpa/speaker/profile_store.py` (`EagleProfileStore`) | **Untouched.** |
| `_picovoice_access_key()` in `server.py` | **Untouched.** Only called from the two `eagle`/`porcupine` branches now — dead code on the default path, live code on the opt-in path. |
| `pyproject.toml`'s `wake-porcupine` / `speaker-id` groups | **Untouched.** Still there, still installable, just not part of a default install (never were). |

What actually changes: the **defaults** in `config_schemas.py` /
`config/audio.yaml` (`wake_engine`, `speaker_id.backend`), plus the two
new adapter files and their config fields. Picovoice support doesn't
regress — it's one YAML edit plus `pip install pvporcupine pveagle` plus
an access key away, exactly as before.

## 6. Key design decisions and rationale

| Decision | Rationale |
|---|---|
| No new ports; reuse `WakeWordPort`/`SpeakerRecognitionPort`/`SpeakerEnrollmentPort` as-is | Proves the original ISP boundary was drawn correctly — a second, unrelated vendor family fits the same three Protocols with zero modification. |
| `backend` field on `SpeakerIdConfig` rather than a separate config section per backend | Mirrors `wake_engine`'s existing single-string-selector pattern (`none`/`hotkey`/`porcupine`→ now `+openwakeword`); one axis to reason about, not a config section per vendor. |
| Resemblyzer buffers audio and recomputes on a window, caching the last score between computations | Resemblyzer's embedding model has no per-frame API at all — a window is structurally required. Caching reuses exactly the shape `VoiceSession` already expects (it accumulates scores into a running mean itself; a port that updates its answer every N calls is compatible with no caller change). |
| Enrollment averages several short sub-utterance embeddings instead of embedding the whole recording at once | Standard d-vector practice — more robust to a single noisy stretch of audio than one long embedding; mirrors Eagle's own internal practice of incorporating feedback across the whole enrollment session rather than one shot. |
| `match_threshold` kept as one shared field rather than a threshold-per-backend config split | Avoids designing a dual-threshold scheme for a case that's simple to handle operationally: switching backend requires re-tuning one number, which is already true in spirit (Eagle's own score scale was never claimed to be portable to anything else). Documented explicitly in the YAML comment so it isn't a silent trap. |
| `wake_engine` default changed from `hotkey` to `openwakeword` | `hotkey` depends on `pynput`'s global OS keyboard hook, which does not work on headless Raspberry Pi OS Lite (no X server) — already flagged in `docs/07-backlog.md` as a real blocker for the actual deployment target. `openwakeword` is frame-driven like Porcupine, needs no keyboard hook, and works headless — so this change both removes the Picovoice dependency *and* fixes the headless blocker in one move. |
| "Hey Veda" trained via synthetic TTS data rather than recorded human speech | Avoids needing hundreds of real recordings before a first working model exists; openWakeWord's own documented approach for exactly this situation. Reuses this project's already-staged Piper TTS engine as the synthesizer instead of introducing a new tool just for training data. Real-recording fine-tuning is left as a later, separate pass (§2 non-goals) once live hardware is reachable. |
| Distinct file extension (`.resemblyzer.npy` vs. `.eagle`) for profile storage, same directory | Lets both backends coexist in `data/speaker_profiles/` without collision, so flipping `backend` back and forth in config never requires migrating or deleting enrolled profiles. |
| `torch` accepted as a dependency for Resemblyzer despite its size | Resemblyzer's actual model is small (~17MB); torch is the heavy part (hundreds of MB). This is a real tradeoff against Eagle (tiny, embedded, no ML framework) — flagged explicitly in §7 as something to verify on real Pi hardware rather than assumed fine. If it proves too heavy at provisioning time, the fallback is staying on `backend: eagle` with a key, or a future lighter embedding model — the `backend` selector already supports that without further redesign. |

## 7. Verification plan (not yet executed)

This section lists what "verify it," the user's explicit next step,
should cover once this design is approved and implemented — none of this
has been run yet:

1. **Install check**: `pip install openwakeword onnxruntime resemblyzer torch`
   on the Windows dev venv (Python 3.12, already set up this session) —
   confirm no build-from-source issues (unlike the earlier
   `llama-cpp-python` saga, all four of these ship prebuilt wheels for
   Windows, so this is expected to be uneventful, but should be confirmed
   rather than assumed).
2. **ARM/Pi wheel availability**: confirm `torch` and `onnxruntime` both
   publish prebuilt `aarch64`/`manylinux` wheels for Raspberry Pi OS Lite
   64-bit's Python version — this is the one real open question for the
   actual deployment target, separate from whether it installs cleanly
   on Windows.
3. **Run the §4.8 training pipeline** to actually produce
   `hey_veda.onnx` — nothing downstream of this is testable before this
   step exists; it's the one piece in this design that creates a new
   artifact rather than just wiring up existing ones.
4. **File-based dry run, no live mic needed**: feed `OpenWakeWordEngine`
   a canned WAV containing "Hey Veda" (synthesized with the same TTS
   voice used for training, as a sanity check, then ideally with a real
   recorded voice) and expect `True`; a canned WAV of unrelated
   speech/silence should give `False` — same file-driven testing style
   already used to confirm the STT/LLM models after staging them.
5. **Unit tests mirroring existing style**: new tests parallel to
   `tests/test_wake_gating.py` / `tests/test_speaker_gating.py`, but
   exercising the new adapters directly (not `VoiceSession`, since that
   integration is unchanged and already covered).
6. **Config load check**: confirm `load_full_config()` still produces
   valid defaults with the new fields, and that the existing
   `config/audio.yaml` (post-edit) round-trips through Pydantic cleanly —
   same "purely additive" check the original speaker-ID design used.
7. **Live hardware pass** (once reachable): enroll one speaker via
   Resemblyzer, speak "Hey Veda," confirm an end-to-end
   trigger→listen→recognize→reply cycle — the actual proof this replaces
   Picovoice functionally, not just on paper, and the real test of
   whether the synthetic-data-trained model holds up against an actual
   human voice rather than its own TTS training data.

## 8. Known limitations / open questions

- Resemblyzer's cosine-similarity score distribution has not been
  measured against this project's actual microphone/room — the `0.6`
  default `match_threshold` is carried over from Eagle's tuning and is
  very likely wrong for Resemblyzer's scale. Flagged in §6; needs a real
  recording session to tune, not guessable from documentation alone.
- **Confirmed, not just predicted**: the trained "Hey Veda" model has
  measured accuracy problems. Tested against synthetic speakers it never
  saw during training (§4.8.2): roughly 50% recall (2 of 4 new-speaker
  "hey veda" clips triggered) and a real false-positive case ("turn on
  the lights" incorrectly triggered it). It has also never heard a real
  human say the phrase at all — every test so far is synthetic TTS
  testing a synthetic-TTS-trained model. Both gaps point the same
  direction: this is proof the pipeline works, not a production-ready
  model. The concrete next step is a retrain with more samples and/or
  the full ACAV100M negative set (§4.8.2's closing paragraph), followed
  by testing against real recorded speech.
- `torch`'s footprint on an 8GB Pi 5 running the existing STT
  (faster-whisper) and LLM (llama.cpp, Qwen3-4B-Q4_K_M) simultaneously is
  an unverified RAM-budget question — three local ML runtimes resident
  at once (torch, onnxruntime, llama.cpp) is more total overhead than the
  previous Eagle/Porcupine setup (both tiny, non-ML-framework native
  libs). Worth a real memory-pressure test before committing to this as
  the permanent default, not just the dev-time default.
- The training pipeline in §4.8 has now been run once (2026-10-03,
  first pass: 2000+400 positive / 4000+800 negative synthetic clips, MIT
  RIR only, no FMA, no ACAV100M, 10,000 training steps). Exact sample
  counts, step counts, and whether to add FMA/ACAV100M for a retrain
  remain open tuning questions — but they're now informed by one real
  run's measured accuracy (§4.8.2) rather than being untested guesses.
