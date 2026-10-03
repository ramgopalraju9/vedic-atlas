# Backlog — known gaps, not yet actioned

This file tracks real gaps discovered while running the system, that are
deliberately **not being fixed yet** — recorded here so they aren't
re-discovered from scratch next time, and so a decision to act on one of
them is a conscious choice, not a surprise. Nothing in this file has been
implemented. Remove an entry once it's actually done (and say so in the
commit, not here).

## Voice — wake-word engine & speaker recognition — Picovoice replaced, implemented, awaiting dependencies + verification

**Decision (2026-10-03)**: the user ruled out Picovoice entirely — no
account, no API key, for either the wake-word engine or speaker ID. Both
Picovoice adapters (`tpa/wake_word/porcupine.py`,
`tpa/speaker/eagle_speaker_recognizer.py` / `eagle_speaker_enroller.py`)
**stay in the codebase untouched** and remain selectable by config for a
possible future switch-back — they are just no longer the default, and
their dependency groups (`wake-porcupine`, `speaker-id`) are not part of
a default install (they weren't before either).

**Replacement, implemented (2026-10-03)**: see
[`docs/voice/open-source-wake-speaker-design.md`](voice/open-source-wake-speaker-design.md)
for the full design — **openWakeWord** (local ONNX wake-word detection)
replacing Porcupine, and **Resemblyzer** (local PyTorch speaker-embedding
model) replacing Eagle. Both are Apache-2.0, need no API key, run fully
offline. Code now in place:

- `tpa/wake_word/openwakeword_engine.py` — `OpenWakeWordEngine`
- `tpa/speaker/resemblyzer_speaker_recognizer.py` — `ResemblyzerSpeakerRecognizer`
- `tpa/speaker/resemblyzer_speaker_enroller.py` — `ResemblyzerSpeakerEnroller`
- `tpa/speaker/resemblyzer_profile_store.py` — `ResemblyzerProfileStore`
- `server.py`'s `_build_wake_word()` / `_build_speaker_id()` /
  `_build_speaker_enrollment_service()` updated with the new branches
- `config_schemas.py` / `config/audio.yaml` updated — `wake_engine` now
  defaults to `openwakeword`, `speaker_id.backend` now defaults to
  `resemblyzer`
- `pyproject.toml` — new `wake-openwakeword`, `wake-openwakeword-training`,
  `speaker-id-resemblyzer` optional groups

All existing ports (`WakeWordPort`/`SpeakerRecognitionPort`/
`SpeakerEnrollmentPort`) unchanged, as designed. One real bug caught and
fixed during implementation (not present in the design doc's final
version): `VoiceSession._feed_speaker_frame()` treats
`frame_length <= 0` as "this port is disabled, skip it" — the design's
first draft had Resemblyzer report `frame_length: 0` since it's not
frame-size-sensitive, which would have silently disabled speaker ID
entirely. Fixed to report a real nonzero chunk size (1280 samples/80ms,
arbitrary but nonzero).

Verified so far (no new dependencies installed yet, by design — the user
is installing them separately): `pyproject.toml` parses, `config/audio.yaml`
parses and loads through Pydantic with the new fields, all new/edited
`.py` files pass `py_compile`, and `import server` succeeds standalone
(every new dependency is imported lazily inside its builder function, so
the module itself loads fine before `openwakeword`/`resemblyzer`/`torch`
are installed — same degrade-on-missing-package pattern as every other
adapter in this project).

**Not yet done**: actually installing `openwakeword`/`onnxruntime`/
`resemblyzer`/`torch` (in progress, by the user) and everything in the
design doc's §7 verification plan (ARM/Pi wheel availability, unit tests,
config round-trip against a real boot, live hardware pass).

**Wake phrase: "Hey Veda" — trained and staged 2026-10-03, but low accuracy, not production-ready.**
A first model now exists at `data/models/openwakeword/hey_veda.onnx`
(+ its required `.onnx.data` sidecar file — both must be staged
together, easy to miss since the `.onnx` file alone looks complete) and
the real server loads and activates it (`wake engine 'openwakeword'
active (hey_veda)`, confirmed via a real boot). But tested against the
real `OpenWakeWordEngine` adapter with fresh synthetic clips: **~50%
recall** (2 of 4 new-speaker "hey veda" clips triggered) and a **real
false positive** ("turn on the lights" incorrectly triggered it). This
is proof the training pipeline works end-to-end, not a model ready for
real use — see design doc §4.8.2 for the full numbers and §8 for why.
Next step: retrain with more samples and/or the full ACAV100M negative
set below, then test against real recorded speech, not just synthetic.

Design doc §4.8 has been rewritten with what actually happened attempting
this, not left as originally planned — eight Windows-specific
compatibility gaps (§4.8.1) plus two real surprises worth knowing before
retraining:

1. **`piper-sample-generator` (the tool openWakeWord's own notebook uses)
   doesn't install on this machine at all** — its `piper-phonemize`
   dependency ships no Windows wheel, and its Linux wheels cap at Python
   3.11 (this venv is 3.12). Confirmed directly via PyPI/GitHub-releases
   listings, not assumed. Worked around by synthesizing clips with the
   modern `piper-tts` package instead (already installed, Windows-
   compatible) against the same underlying multi-speaker voice
   (`en_US-libritts_r-medium`, 904 speaker IDs) — same voice variety,
   different (working) tool.
2. **The official negative-feature file is 17.3GB**, not "several GB" as
   first estimated — confirmed via `HEAD` request
   (`openwakeword_features_ACAV100M_2000_hrs_16bit.npy`, exactly
   17,280,000,128 bytes). **Decision: train the first model without it**
   (synthetic negatives + ~1hr FMA music only), accepting a real
   quality tradeoff — higher false-positive risk on real household
   sounds the model never saw — in exchange for a much faster first
   pass. Download link for a later full-recipe retrain:
   `https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy`

Also answered directly: **why not just repurpose faster-whisper (already
staged) instead of training a separate wake-word model?** Because
`VoiceSession._tick()` calls STT once per detected utterance, never
per-frame — a wake-word model's entire job is running cheaply on *every*
frame continuously, which Whisper isn't shaped for. Running Whisper
continuously instead is exactly what `wake_engine: none` already does
(tested earlier this session) — it works, but defeats the purpose of
having a cheap gate at all. Full reasoning in design doc §4.8.

Training workspace: `wakeword_training/` at repo root (gitignored — holds
cloned tooling + downloaded datasets, dev-machine-only, never deployed to
the Pi; only the final `hey_veda.onnx` leaves this directory).

**Still true, carried over from the original note**: speaker-ID
enrollment is inherently a live, in-person action regardless of backend
— nothing to stage ahead of time for that part either way.

## Memory — semantic recall layer dormant

**Problem**: `config/embedding.yaml` has `enabled: true`, and the whole
semantic-recall pipeline (`tpa/inference/embedding_adapter.py` →
`FastEmbedProvider`, `tpa/persistence/vector_store.py` →
`SqliteVectorStore`, `MemoryIndexer`/`SemanticRecall` in
`service/memory/`) is wired into `ResponderAgent`, but `fastembed` isn't
installed on this machine (`[embedding] unavailable: No module named
'fastembed'` on every boot) and the `BAAI/bge-small-en-v1.5` ONNX model
isn't staged at `data/bge-small-en-v1.5`.

**Current behavior without it**: this degrades cleanly, not brokenly —
taught facts (`/api/knowledge`, `JsonKnowledgeStore` at
`data/knowledge.json`) and conversation history/summarization
(`ConversationSummariser`) both work fully right now. What's missing is
specifically *similarity-based* retrieval — the assistant can't yet pull
up a fact or old summary by meaning when the user doesn't phrase it the
same way it was taught.

**Intended fix**: `pip install fastembed` (already scoped under the
`embedding` optional-dependency group in `pyproject.toml`) + download the
`BAAI/bge-small-en-v1.5` ONNX model to `data/bge-small-en-v1.5`, same
staging pattern already used for the STT and LLM models this session.

**Not done because**: no external blocker here (no account/key needed,
just a model download) — this is the most "ready to just do" item on this
list whenever the user wants it tackled.

## Summary — what needs what before it can be picked up

| Item | Needs | Blocker type |
|---|---|---|
| openWakeWord wake-word (Picovoice replacement) | implemented, trained, staged, boots real server — needs a retrain with more data for usable accuracy (currently ~50% recall + false positives) | first pass done — needs a better retrain |
| Resemblyzer speaker recognition (Picovoice replacement) | implemented — install `resemblyzer`+`torch` (in progress), then live enrollment per person | code done — needs deps + verification, plus in-person enrollment |
| Porcupine / Eagle (Picovoice originals) | kept in codebase, config-selectable; needs account + access key if ever switched back to | external account — parked, not planned |
| Semantic memory recall (fastembed) | `pip install fastembed` + stage `bge-small-en-v1.5` model | none — pure staging work |
