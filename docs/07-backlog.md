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

## Memory — semantic recall: enabled (hotfix, 2026-10-04)

**Trigger**: the user told Veda their favourite sweet; a day later it could not recall it. The statement *was*
stored (conversation turn, then a conversation summary), but only the newest 3 summaries reach the chat prompt and
the meaning-based recall that should find older ones was off: `fastembed` was not installed, the embedding model
was never staged, and the adapter passed a folder path where fastembed needs a model name.

**Fixed**: `tpa/inference/embedding_adapter.py` now runs the staged BGE-small ONNX model directly with
`onnxruntime` + `tokenizers` (no fastembed); `tokenizers` is a declared dependency; `scripts/setup_pi.sh` downloads
`model.onnx` + `tokenizer.json` into `data/bge-small-en-v1.5/`. At startup `_backfill_memory_index` indexes all
existing facts and summaries. Verified on the user's real database: all 12 summaries indexed, and "what is my fav
sweet?", "which sweets do I like?", "do you remember what dessert I love" and "what did I say about gulab jamun?"
(stored as "gamun") all rank the right summary first and Veda answers correctly.

**Known limits (not fixed)**
- Summaries are long and cover many topics, so their embeddings are blurry: unrelated questions still score 0.45-0.60
  against some of them. `top_k: 3` / `min_score: 0.5` (down from 5 / 0.3) is a compromise between recalling the right
  summary (0.53-0.72 on real data) and not stuffing the prompt with noise (each recalled summary costs about 100-150 tokens).
- ~~Nothing saves a preference as a permanent fact.~~ Done: the `remember` tool (see `08-tool-harness.md`, "The remember
  tool") saves "topic: value" facts that are always in the chat prompt. Facts are saved only when the user states or asks
  for them; there is no automatic fact extraction from conversation yet (the summariser still only writes summaries).
- The speech recogniser's spelling is what gets stored ("gulab gamun"); recall works through meaning, so this still matches.

## Summary — what needs what before it can be picked up

| Item | Needs | Blocker type |
|---|---|---|
| openWakeWord wake-word (Picovoice replacement) | implemented, trained, staged, boots real server — needs a retrain with more data for usable accuracy (currently ~50% recall + false positives) | first pass done — needs a better retrain |
| Resemblyzer speaker recognition (Picovoice replacement) | implemented — install `resemblyzer`+`torch` (in progress), then live enrollment per person | code done — needs deps + verification, plus in-person enrollment |
| Porcupine / Eagle (Picovoice originals) | kept in codebase, config-selectable; needs account + access key if ever switched back to | external account — parked, not planned |
| Semantic memory recall | done 2026-10-04 (see "Memory — semantic recall: enabled"); the `remember` tool for explicit facts is done; automatic fact extraction is still open | none |

## Tool harness — known gaps (see 08-tool-harness.md)

- **Weather is current conditions only** — no forecast or rain probability (Open-Meteo's daily/hourly fields are unused).
- **Follow-up fragments** ("and in Mumbai?") are routed by the LLM router using the recent turns; verified live for weather but not part of the eval set (the eval has no conversation history).
- **LLM router misroutes** about 1 in 28 unmatched messages ("I feel like going for a walk" -> tasks); the tasks agent hands it back to chat, costing one extra model call.
- **"I need to ..." always means a task** — "I need to open chrome" routes to the tasks agent, not the system agent.
- **CLI `/task` parity** — `/task add` has no duplicate check, there is no `/task delete`, and `/task done` needs a numeric id (chat accepts a phrase).
- **Summariser vs. live chat** — both share one model behind `SingleFlight`; a summarising tick delays a live reply. Yielding to chat is not implemented.
- **Search quality** is Tavily's: snippets only, no page reading. An "open this link" tool would need its own allow-list and limits.
- **`tests/test_vector_store.py`** has a syntax error on line 7 (`import import`) and cannot be collected.
- **Old conversation history** may still contain wrong earlier replies; only "Task added"-style claims are filtered from the chat prompt.

## Voice — wake word model quality (measured 2026-10-03)

`config/audio.yaml` now uses `wake_engine: openwakeword` with the custom-trained `hey_veda.onnx`. Scored against
synthetic SAPI speech (two Windows voices) it is **not reliable**: "Hey Veda" triggered on one voice (0.77) and not the
other (0.001); "Hello there" falsely triggered (0.85) and "subscribe to our channel" falsely triggered (0.62); silence and
white noise were fine (0.001). No threshold separates the hits from the false triggers on that data. Synthetic voices differ
from the model's training voices, so this is not conclusive for a real voice, but it needs verifying:

- Run `python scripts/wake_test.py` (stop the server first) and say "Hey Veda", ordinary sentences, then stay quiet; pick `wake_threshold` from what you see.
- If it still misses or false-triggers, retrain with real recordings of the user's voice and many hard negatives (common phrases, TV/YouTube speech). The training run in `wakeword_training/` ended with an `onnx_tf` import error after exporting the ONNX; the ONNX itself is what is used.
- "Hey Veda what is the weather" (no pause after the wake phrase) scored ~0.002 — the model seems to need the phrase to stand alone.
