# Multi-Speaker Voice Recognition — Design Document

**Status**: Implemented and tested (software-level). Not yet validated
against real hardware/voices on a Raspberry Pi 5 or with a real Picovoice
access key.

## 1. Context

The make-a-thon problem statement's single most-emphasized requirement is
that the assistant must **not react to stray/ambient talk** — only to
things actually directed at it. When this was scoped out with the user,
two things were confirmed:

1. Voice recognition should be **multi-speaker** — identify which of
   several enrolled household members is talking, not a single-owner
   voice lock.
2. The assistant should feel **more proactive**, not require the wake
   word on every single turn.

The existing wake-word + "armed listening window" mechanic in
`service/voice/voice_session.py` already covers most of requirement #2 —
an utterance re-arms the window, so a natural back-and-forth doesn't need
the wake word repeated. What was missing was any notion of *whose* voice
triggered an utterance at all. This document covers the design that fills
that gap: **multi-speaker recognition via Picovoice Eagle**, chosen by the
user because it's the same vendor/licensing family as the already-wired
Porcupine wake-word engine, and vendor-proven for Pi-class ARM hardware.

## 2. Goals / non-goals

**Goals:**
- Identify which enrolled speaker produced an utterance, with a
  confidence score.
- Use that identity as an optional additional stray-talk filter —
  configurable, not forced on.
- Lay the groundwork for per-speaker personalization later (the data is
  captured; using it in `ResponderAgent` is a deliberate follow-up, not
  part of this change).
- Ship as a zero-risk addition: with the feature disabled (the default),
  behavior is byte-for-byte identical to before this change.

**Non-goals (explicitly out of scope for this design):**
- Replacing the wake-word mechanic. Speaker-ID layers on top of the
  existing armed-window flow; it doesn't replace it.
- Open-mic "always listening, no wake word needed" operation. That would
  require a much harder addressee-detection problem (is this utterance
  actually directed at the device, vs. two enrolled humans talking to
  each other) which this design does not attempt.
- Live coexistence between enrollment and the always-on voice session —
  see §6's documented precondition.

## 3. Integration with the current system

### 3.1 Where this sits in the architecture

This repo is a ports-and-adapters (hexagonal) system — see
`docs/00-overview.md`/`docs/01-domain-layer.md` for the general pattern.
This feature follows it exactly:

```
domain/ports/speaker_recognition_port.py   <- hot-path contract
domain/ports/speaker_enrollment_port.py    <- setup-lifecycle contract
         ▲                    ▲
         │ implements          │ implements
tpa/speaker/eagle_speaker_recognizer.py    tpa/speaker/eagle_speaker_enroller.py
         ▲                                            ▲
         │ injected                                    │ injected
service/voice/voice_session.py          service/speakers/speaker_enrollment_service.py
         ▲                                            ▲
         │ wired at boot                                │ wired at boot
src/server.py (composition root: _build_speaker_id, _build_speaker_enrollment_service)
                                                         ▲
                                              controller/routes/speakers.py
```

Nothing in `service/` imports `tpa/` directly — `VoiceSession` and
`SpeakerEnrollmentService` only ever see the two new Protocol types.
Concrete Eagle adapters are constructed exactly once, in `server.py`'s
`bootstrap(app)`, exactly like every other adapter in the system
(`WakeWordPort`→`PorcupineWakeWord`, `STTPort`→`FasterWhisperProvider`,
etc.).

### 3.2 Why two ports, not one

`SpeakerRecognitionPort` (hot path — `VoiceSession` calls `process()`
every frame while armed) and `SpeakerEnrollmentPort` (cold path — called
only by the enrollment service, during an occasional admin action) are
deliberately separate Protocols. This mirrors the existing `WakeWordPort`
precedent (a minimal hot-path surface) and is an Interface Segregation
Principle decision: `VoiceSession` is structurally incapable of calling
`enroll_*` methods it has no business touching, because its only
reference is typed as `SpeakerRecognitionPort`.

The two ports are implemented by two separate adapter classes in
`tpa/speaker/` (`EagleSpeakerRecognizer`, `EagleSpeakerEnroller`), even
though both wrap the same vendor package (`pveagle`). This is because
Eagle's own SDK models them as two different native objects
(`EagleProfiler` for enrollment, `Eagle` for recognition) with
non-overlapping lifecycles — one-shot setup vs. long-lived hot path.
Bundling them into one adapter class would blur exactly the distinction
the two-port split exists to keep clean.

### 3.3 Where it plugs into `VoiceSession`'s loop

`VoiceSession._tick()` already has an established frame-buffering idiom
for a vendor SDK that needs fixed-size frames fed continuously while
armed — the wake-word engine (`_wake_triggered`, slicing a running byte
buffer into `frame_length`-sized chunks). Speaker-ID reuses the identical
idiom (`_feed_speaker_frame`), called right alongside it:

```
mic frame
   │
   ▼
muted? ──yes──> reset everything, sleep
   │no
   ▼
speaking (TTS playing)? ──yes──> skip (echo prevention)
   │no
   ▼
armed (wake window open, or no wake engine configured)?
   │no ──> only check wake trigger, return
   │yes
   ▼
_feed_speaker_frame(frame)   <- accumulates running per-speaker score
   │
   ▼
UtteranceCollector.push(frame)
   │
   ▼ (utterance complete)
_handle(utterance)
   │
   ├─ transcribe (STT)
   ├─ _resolve_speaker()   <- mean score over the whole utterance, once
   ├─ require_known_speaker & unknown? ──yes──> drop, log, return
   │                                    │no
   ├─ echo-guard check
   └─ AgentContext.metadata["speaker_name"] = name  ──> supervisor.execute(ctx)
```

**Score continuously, decide once** — not a frame-by-frame reject. Eagle's
scores stabilize with more audio; aborting on the first low-scoring frame
risks clipping legitimate speech from an enrolled speaker (a cough, a
quiet start, a half-second of overlap), which would directly undermine
the goal — the point is to correctly recognize the right person, not just
to reject fast. So frames are scored and summed throughout collection,
and the accept/reject decision happens exactly once, in `_handle()`,
off the accumulated mean.

### 3.4 Composition root wiring (`server.py`)

Two new builder functions, following the exact shape of every existing
`_build_*` function in this file (construct, catch everything, degrade to
`None`/disabled on any failure — never crash boot):

- `_build_speaker_id(cfg)` — builds `EagleSpeakerRecognizer` if
  `audio.speaker_id.enabled` and an access key is present; `None`
  otherwise. Stored at `app.state.speaker_recognizer` (reachable from
  both the voice session and the enrollment route's `reload_profiles()`
  call) and passed into `_build_voice_session(..., speaker_id=...)`.
- `_build_speaker_enrollment_service(cfg, audio_capture)` — builds
  `SpeakerEnrollmentService` wrapping an `EagleSpeakerEnroller` and the
  *same* shared `AudioCapturePort` instance `CaptureGate`/`VoiceSession`
  already use. Stored at `app.state.speaker_enrollment_service`.
- A new `_picovoice_access_key()` helper centralizes reading
  `PICOVOICE_ACCESS_KEY` (falling back to the existing
  `PORCUPINE_ACCESS_KEY`) — `_build_wake_word` was updated to use it too,
  since Picovoice issues one key valid for every one of their SDKs.
- `speakers` router registered in the same loop as every other route
  module, mounted under `/api` like everything else.
- Shutdown: `app.state.speaker_recognizer.stop()` releases Eagle's native
  resources, added to `lifespan()`'s teardown sequence alongside the
  existing `CaptureGate`/`VoiceSession`/`ConversationSummariser` stops.

## 4. New components, in detail

### 4.1 `domain/ports/speaker_recognition_port.py`

```python
class SpeakerRecognitionPort(Protocol):
    frame_length: int            # property
    sample_rate: int             # property
    speaker_names: list[str]     # property, order matches process()'s scores
    def process(self, frame: bytes) -> list[float]: ...
    def reload_profiles(self) -> None: ...
```

`process()` returns one score (0.0–1.0) per enrolled speaker, in the same
order as `speaker_names` — this mirrors Eagle's real API shape rather
than flattening it to a single best-match return; the caller does the
argmax/threshold itself (`VoiceSession._resolve_speaker()`).

### 4.2 `domain/ports/speaker_enrollment_port.py`

```python
class SpeakerEnrollmentPort(Protocol):
    frame_length: int
    sample_rate: int
    def enroll_feed(self, frame: bytes) -> tuple[float, str]: ...  # (percentage, feedback)
    def enroll_reset(self) -> None: ...
    def enroll_finish(self, speaker_name: str) -> None: ...        # raises if <100%
    def list_enrolled(self) -> list[str]: ...
    def delete_enrolled(self, speaker_name: str) -> bool: ...
```

`list_enrolled`/`delete_enrolled` live here (not on the recognition port)
because they're part of managing the enrollment directory, which this
port already owns — `SpeakerEnrollmentService` needs them for duplicate-
name checks and the `DELETE /api/speakers/{name}` route, and per the
"service never imports `tpa` directly" rule, that has to go through a
port it already depends on rather than reaching for a new `tpa` import.

### 4.3 `tpa/speaker/` adapters

Built against the **actual installed `pveagle` package**, inspected
directly rather than trusted from vendor documentation — this caught two
real discrepancies worth recording:

- `Eagle.process()`'s own docstring references a `.frame_length` property
  that does not exist on the recognizer object. The real property is
  `.min_process_samples`. The adapter hides this vendor inconsistency
  behind the port's one clean `frame_length` name.
- `pveagle.create_recognizer()` does **not** take a `speaker_profiles`
  argument at construction (despite the function's own docstring implying
  it does) — profiles are passed on every `.process(pcm, speaker_profiles)`
  call instead. This means the recognizer object is profile-agnostic; the
  adapter holds the current profile list in memory and `reload_profiles()`
  just re-reads it from disk, with no need to recreate the native
  recognizer.
- `EagleProfiler.enroll()` returns a bare `float` percentage, not a
  `(percentage, feedback)` tuple — there is no `EagleProfilerEnrollFeedback`
  type in this SDK version. `enroll_feed()` synthesizes a plain-English
  feedback string from percentage buckets so the port's richer contract
  is still satisfied without inventing vendor data that doesn't exist.

Three files:

- **`eagle_speaker_recognizer.py` — `EagleSpeakerRecognizer`**: loads
  all persisted profiles at construction via `EagleProfile.from_bytes()`.
  Zero profiles is not a failure — `process()` short-circuits to `[]`
  without even calling the native library.
- **`eagle_speaker_enroller.py` — `EagleSpeakerEnroller`**: wraps one
  `EagleProfiler`, `.reset()` between sessions. `enroll_finish()` calls
  `.export()`, which the vendor SDK itself raises on if enrollment hasn't
  reached 100% — that exception propagates naturally, satisfying the
  port's documented "raises if incomplete" contract with zero extra code.
- **`profile_store.py` — `EagleProfileStore`**: persists one file per
  speaker at `data/speaker_profiles/<name>.eagle` (raw exported profile
  bytes). A directory of blobs, not a DB table — same shape as the
  existing `.ppn` wake-word keyword-file precedent. Has no `pveagle`
  import itself; the two adapters above own the bytes↔`EagleProfile`
  conversion.

Both adapters degrade to "unavailable" on any construction failure
(missing package, missing/invalid key, native init error) — never crash
boot, identical shape to `_build_wake_word`.

### 4.4 `service/speakers/speaker_enrollment_service.py`

Thin facade, same shape as `service/tasks/task_service.py` over
`TaskRepositoryPort`: depends only on `SpeakerEnrollmentPort` +
`AudioCapturePort`, no `tpa` import, no `VoiceSession` dependency.

**Mic-sharing precondition, stated explicitly rather than engineered
around**: enrollment reads from the *same* `AudioCapturePort` instance
the always-on voice loop polls. Two concurrent readers would race on
`read()`. The enrollment route (`controller/routes/speakers.py`'s
`_check_mic_available`) requires the voice session stopped (`POST
/api/voice/stop`) **and** the capture gate unmuted (`POST
/api/privacy/mute {"muted": false}`) before enrollment can start — both
checked directly against `app.state`, raising a clear `AppException`
otherwise. A shared-mic arbitration layer would remove this constraint
but adds real complexity for what is an occasional admin action, not a
hot path; not worth it at this stage.

Error handling: every failure mode in this service reuses an existing
`ExceptionCode` — no new codes were added. Blank name, duplicate name,
calling `feed`/`finish` out of order, incomplete enrollment →
`VALIDATION_ERROR` (422). Native/export failure → `SYSTEM_ERROR` (500).
Deleting a name that doesn't exist → `NOT_FOUND` (404).

### 4.5 `controller/routes/speakers.py`

A separate route group (like `/privacy` is separate from `/voice`), not
bolted onto the existing voice routes — enrollment is a distinct
resource/lifecycle from the always-on session.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/speakers` | List enrolled speaker names |
| `POST` | `/api/speakers/enroll/start` | Begin a session for `{name}` (checks mic precondition) |
| `POST` | `/api/speakers/enroll/feed` | Feed ~1s of live mic audio, returns `{percentage, feedback}` |
| `POST` | `/api/speakers/enroll/finish` | Export + persist the profile, reload the live recognizer |
| `POST` | `/api/speakers/enroll/cancel` | Discard the in-progress session |
| `DELETE` | `/api/speakers/{name}` | Remove a profile, reload the live recognizer |

### 4.6 Config (`audio.speaker_id`, purely additive)

```yaml
speaker_id:
  enabled: false
  profiles_dir: data/speaker_profiles
  match_threshold: 0.6
  require_known_speaker: false
```

- `enabled: false` — feature entirely dormant by default.
- `require_known_speaker: false` — even once enabled, unrecognized voices
  are only *tagged*, never *blocked*, until explicitly turned on. This
  avoids a footgun: flipping `require_known_speaker: true` before anyone
  has enrolled would mean the assistant responds to no one.
- `match_threshold: 0.6` — minimum mean Eagle score to count as a match;
  tunable per-deployment (room acoustics, mic quality) without a code
  change.

## 5. Key design decisions and rationale

| Decision | Rationale |
|---|---|
| Score continuously, decide once (mean over utterance) | A single noisy frame must not reject a real enrolled speaker — the whole point fails if "don't react to stray talk" also means "randomly ignores the right person." |
| `process()` called synchronously inline, not `asyncio.to_thread` | Eagle's per-frame cost (vendor-positioned for microcontroller-class hardware) is well under `to_thread`'s dispatch overhead at the ~31 calls/sec cadence a 32ms frame implies. Matches the existing wake-word precedent, which makes the same call inline for the same reason. |
| Runs only while armed/collecting, never during LLM generation or TTS playback | `_tick` already short-circuits to a cheap sleep during both of those — no new contention is introduced on the Pi's limited CPU budget. |
| Two ports, two adapter classes | Interface Segregation — hot path vs. cold path have genuinely different callers, different lifecycles, different native objects underneath. |
| Env var generalized to `PICOVOICE_ACCESS_KEY` (with fallback) | Picovoice issues one key per account valid for all their SDKs; the old `PORCUPINE_ACCESS_KEY`-only naming was about to become misleading with a second Picovoice SDK in the codebase. |
| No new `ExceptionCode`/`ErrorMessage` entries | Every failure mode in this feature is genuinely a client-input-shape problem (`VALIDATION_ERROR`), a native failure (`SYSTEM_ERROR`), or a missing resource (`NOT_FOUND`) — all pre-existing, correctly-fitting codes. Confirms the "reuse before adding" convention holds without exception here. |
| Enrollment requires voice session stopped + mic unmuted | Avoids a real concurrency bug (two readers racing on one `AudioCapturePort.read()`) without adding a mic-arbitration subsystem for what's an infrequent admin action. |

## 6. Testing performed

- **Static**: all 249 `src/*.py` files compile; the module imports clean
  end-to-end (`import server` with no errors).
- **Boot test, feature disabled (default)**: full `uvicorn` boot, `GET
  /api/speakers` correctly returns `503` ("not initialized"), all 6 new
  routes present in the OpenAPI schema, root `/` responds normally — zero
  change to prior behavior.
- **Degrade-path tests, directly against the real SDK**: `_build_speaker_id`
  with `enabled=True` and no access key → clean warning, `None`, no
  crash. Same with an invalid (fake) access key → the real `pveagle`
  initialization error is caught and logged, `None`, no crash. Both match
  the existing `_build_wake_word` degrade behavior exactly.
- **Unit tests** (`tests/test_speaker_gating.py`, 7 tests, mirroring the
  existing `tests/test_wake_gating.py` style with a scripted fake
  recognizer): frame accumulation and mean-score resolution; below-
  threshold utterances correctly resolve as unknown; no-engine-configured
  path never crashes; scoring state resets correctly between utterances;
  `require_known_speaker=True` drops an unrecognized-voice turn before it
  reaches the supervisor; `require_known_speaker=False` lets it through
  without tagging; a recognized speaker's name lands in
  `AgentContext.metadata["speaker_name"]`.
- **Regression check**: full existing test suite re-run — 13 tests pass
  (including the pre-existing `test_wake_gating.py` and
  `test_task_service.py`), zero new failures introduced. (Two pre-existing,
  unrelated issues were found and left alone, not caused by this change:
  a syntax error in `tests/test_vector_store.py` and a stale fixture
  signature in `tests/test_tool_calling.py`.)

## 7. Known limitations / explicit follow-ups

- **Not validated on real hardware.** Everything above was built and
  tested on a Windows dev machine with `pveagle` installed but no real
  Picovoice access key — the actual recognition accuracy, Pi 5 CPU
  headroom under real load, and mic quality characteristics are unverified
  until this runs on the target device with real enrolled voices.
- **`ResponderAgent` doesn't yet use `speaker_name`.** The hook point
  (`AgentContext.metadata["speaker_name"]`) is live and populated, but no
  agent reads it yet for personalization — that's a natural, cheap
  follow-up, deliberately left out of this change's scope.
- **No live mic-sharing between enrollment and the voice session.**
  Documented as a stated constraint (§4.4), not a bug — enrolling a new
  household member currently requires briefly stopping the always-on
  session.
- **Single confidence threshold, not per-speaker.** `match_threshold` is
  global; a future refinement could tune it per enrolled speaker if some
  voices prove harder to distinguish than others in practice.
