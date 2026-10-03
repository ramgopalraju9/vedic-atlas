# Training the "Hey Veda" wake-word model

A runbook for training, retraining, deploying and verifying the custom wake-word model that
`config/audio.yaml` loads (`wake_engine: openwakeword`). For the design reasoning and the full
history of what went wrong the first time, see
[open-source-wake-speaker-design.md](open-source-wake-speaker-design.md) §4.8. This page is the
practical, repeatable version.

> **Dev-machine only.** Training tooling and datasets are never installed on the Pi. Only two small
> files are deployed (see [Deploy](#7-deploy)).

---

## 1. Summary

| | |
|---|---|
| Engine | [openWakeWord](https://github.com/dscripka/openWakeWord) (open source, runs locally, no API key) |
| Phrase | `"hey veda"` (one phrase; nothing else is trained) |
| Model | a small fully-connected network (`model_type: dnn`, `layer_size: 32`) on top of openWakeWord's pre-trained audio features |
| Files produced | `hey_veda.onnx` (14.6 KB) + `hey_veda.onnx.data` (200 KB) = **about 215 KB** |
| Size after retraining with the 17 GB dataset | **the same, about 215 KB** (the dataset is training input, never stored in the model). Only `layer_size` changes it: 64 is roughly 0.4 MB, 128 roughly 0.8 MB |
| Training workspace | `wakeword_training/` at the repo root (git-ignored, dev-machine only) |
| Deployed to | `data/models/openwakeword/` (also git-ignored, so it is **not** in the repository) |

**Current model quality is weak** (see [Measured quality](#10-measured-quality-of-the-current-model)).
The recommended retrain adds the 17.3 GB negative-speech dataset and recordings of your own voice.

---

## 2. How the pipeline works

```
generate_clips.py        piper-tts voice  ->  synthetic "hey veda" + near-miss phrases   (positive_* / negative_* WAVs)
fetch_background.py      MIT room impulse responses (+ optional background audio)
run_train.py --augment_clips   mix clips with room echo / noise -> openWakeWord audio features
run_train.py --train_model     train the classifier head (10,000 steps + 2 tuning passes) -> export ONNX
                               uses: the generated features, the negative-speech features (ACAV100M),
                                     and validation_set_features.npy to tune the false-positive rate
copy hey_veda.onnx + hey_veda.onnx.data -> data/models/openwakeword/
scripts/wake_test.py     speak into the mic, watch live scores, choose wake_threshold
```

`run_train.py` is a thin wrapper that applies Windows compatibility patches and then runs the
**unmodified** `openwakeword/train.py` (see [Known issues](#11-known-issues-and-why-run_trainpy-exists)).

---

## 3. What is in `wakeword_training/`

| Path | What it is |
|---|---|
| `hey_veda_config.yaml` | training configuration (explained in [section 6](#6-configuration-reference)) |
| `generate_clips.py` | creates synthetic positive and near-miss negative clips with `piper-tts` |
| `fetch_background.py` | downloads the MIT impulse responses (and tries FMA music, see the warning below) |
| `run_train.py` | Windows-safe wrapper around openWakeWord's `train.py` |
| `generate_samples_stub/` | empty stand-in module that `train.py` imports unconditionally (we never use its generator) |
| `piper_voices/` | the text-to-speech voice used to make clips (75 MB) |
| `mit_rirs/` | 270 room impulse responses (6 MB) |
| `validation_set_features.npy` | false-positive validation features (177 MB) |
| `hey_veda/` | outputs: clip folders, feature files and the exported model (230 MB) |
| `train_log*.txt`, `full_log.txt` | logs from earlier runs |

---

## 4. Requirements

- Python 3.12 virtual environment at the repo root (`vedic-atlas-env`), used for training too.
- Packages (training only): `openwakeword>=0.6.0`, `piper-tts>=1.2.0`, `torchinfo`, `torchmetrics`,
  `datasets<4.0` (4.x needs `torchcodec` and FFmpeg), `onnxscript` (ONNX export), plus `torch`,
  `torchaudio`, `scipy`, `soundfile`, `acoustics`, `numpy`, `tqdm`, `pyyaml`.
- **Disk:** about 0.5 GB now. The 17.3 GB dataset adds 17.3 GB, and feature files for more clips add
  a few hundred MB more. A fast SSD matters, because training reads the big file in random batches.
- **RAM:** the 17 GB file is memory-mapped, so it does not have to fit in memory.
- CPU is enough; no GPU is needed for a model this small.

---

## 5. Data and links (all checked on 2026-10-03)

| Data | Used for | Size | Link |
|---|---|---|---|
| **ACAV100M negative-speech features (the "17 GB file")** | teaches the model what *not* to trigger on (about 2,000 hours of ordinary audio, pre-computed) | **17,280,000,128 bytes (17.3 GB)** | https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy |
| Validation-set features | measures false positives per hour while training | 184.8 MB (you have it: `validation_set_features.npy`) | https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy |
| Piper voice model `en_US-libritts_r-medium` (904 speakers) | synthesises the "hey veda" clips | 78.6 MB | https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx |
| Piper voice config | same voice | 20 KB | https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx.json |
| MIT environmental impulse responses | room echo augmentation (downloaded by `fetch_background.py`) | about 6 MB as WAVs | https://huggingface.co/datasets/davidscripka/MIT_environmental_impulse_responses |
| Feature-data repository (browse) | the two `.npy` files above live here | n/a | https://huggingface.co/datasets/davidscripka/openwakeword_features |
| openWakeWord (code, docs) | the engine and the training script | n/a | https://github.com/dscripka/openWakeWord |
| openWakeWord's own training notebook | the reference recipe this follows | n/a | https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb |

**Not used:** `rudraml/fma` (background music). Loading it needs `trust_remote_code=True`, which runs
third-party Python from that repo, so it was declined. See the warning in step 2.

---

## 6. Configuration reference

`wakeword_training/hey_veda_config.yaml` (current values):

| Key | Value | Meaning |
|---|---|---|
| `model_name` | `hey_veda` | output file and runtime model key (`wake_oww_model`) |
| `target_phrase` | `["hey veda"]` | what the model learns to detect |
| `custom_negative_phrases` | `[]` | extra phrases it should *reject* (use this for words that falsely trigger) |
| `n_samples` / `n_samples_val` | `2000` / `400` | synthetic positive clips for training / validation |
| `model_type` / `layer_size` | `dnn` / `32` | network size, which decides the file size |
| `steps` | `10000` | training steps |
| `batch_n_per_class` | `adversarial_negative: 50`, `positive: 50` | examples per class per step |
| `feature_data_files` | `{}` | **where the 17 GB file is plugged in** (empty for the first model) |
| `false_positive_validation_data_path` | `./validation_set_features.npy` | validation negatives |
| `rir_paths` | `["./mit_rirs"]` | room impulse responses |
| `background_paths` | `[]` | no background audio was used |
| `max_negative_weight` | `1500` | how strongly false positives are penalised |
| `target_false_positives_per_hour` | `0.2` | the false-positive rate the tuning passes aim for |
| `output_dir` | `./hey_veda` | outputs |

**Changes for the recommended retrain**

```yaml
feature_data_files:
  ACAV100M_sample: ./openwakeword_features_ACAV100M_2000_hrs_16bit.npy

batch_n_per_class:
  ACAV100M_sample: 1024       # background-speech features read per step
  adversarial_negative: 50
  positive: 50

n_samples: 10000              # more synthetic positives (default run used 2000)
n_samples_val: 2000
custom_negative_phrases:      # add anything that false-triggers in wake_test.py
  - "hello there"
  - "hey vera"
```

---

## 7. Step by step

All commands are for **Windows PowerShell** from the repo root. Use `curl.exe` (not `curl`, which is a
PowerShell alias). `-C -` makes a download resumable.

### 0. Activate the environment

```powershell
cd C:\Users\prave\workspace\buildathon\2026\stg-make-a-thon-21\vedic-atlas
.\vedic-atlas-env\Scripts\Activate.ps1
cd wakeword_training
```

### 1. Voice model (skip if `piper_voices/` already has the two files)

```powershell
curl.exe -L -C - -o piper_voices\en_US-libritts_r-medium.onnx      "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx"
curl.exe -L -C - -o piper_voices\en_US-libritts_r-medium.onnx.json "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx.json"
```

### 2. Room impulse responses (skip if `mit_rirs/` has WAV files)

```powershell
python fetch_background.py
```

> **Warning:** `fetch_background.py` also contains a block that streams FMA music from `rudraml/fma`,
> which requires `trust_remote_code=True` (it executes code from that dataset's repository). That was
> deliberately declined, and the `fma/` folder is empty by design. Run only the MIT RIR part (it skips
> any folder that already has WAVs), or remove the FMA block before running. `background_paths: []`
> in the config matches this.

### 3. Validation features (skip if `validation_set_features.npy` exists)

```powershell
curl.exe -L -C - -o validation_set_features.npy "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy"
```

### 4. The 17 GB negative-speech features (recommended)

```powershell
curl.exe -L -C - -o openwakeword_features_ACAV100M_2000_hrs_16bit.npy "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
```

The file must end up exactly **17,280,000,128 bytes**. Check with `(Get-Item .\openwakeword_features_ACAV100M_2000_hrs_16bit.npy).Length`.
If the download is interrupted, run the same command again; it resumes. Then update
`hey_veda_config.yaml` as shown in [section 6](#6-configuration-reference).

### 5. Generate the synthetic clips

```powershell
python generate_clips.py --output-dir .\hey_veda --n-positive-train 2000 --n-positive-test 400 --n-negative-train 4000 --n-negative-test 800
```

For the larger retrain use `--n-positive-train 10000 --n-positive-test 2000` (and scale the negatives
similarly). The script skips folders that already have 95% of the requested clips, so it is safe to re-run.
It writes 16 kHz mono WAVs to `hey_veda/positive_train`, `positive_test`, `negative_train`, `negative_test`.
The negatives are near-miss phrases ("hey vera", "hello there", "okay veda", "what time is it" ...).

### 5b. (Strongly recommended) add your own voice

Synthetic voices are the main reason the current model is unreliable. Record real clips and drop them
into the same folders:

- **30 to 50 clips of you saying "Hey Veda"**: different distances, loudness and tone; some in a quiet
  room, some with the TV or fan on. Save as **16 kHz, mono, 16-bit WAV** into `hey_veda\positive_train`
  (about 20% of them into `positive_test`).
- **100+ clips of you saying ordinary sentences** (and a few things that sound a bit like the wake
  phrase) into `hey_veda\negative_train` and `negative_test`.

### 6. Augment and compute features

```powershell
python run_train.py --training_config hey_veda_config.yaml --augment_clips
```

Add `--overwrite` if you changed the clips and want the features rebuilt.

### 7. Train and export

```powershell
python run_train.py --training_config hey_veda_config.yaml --train_model
```

Output: `hey_veda\hey_veda.onnx` **and** `hey_veda\hey_veda.onnx.data`. A final error about
`onnx_tf` after the export (the TFLite conversion) is harmless: Veda uses the ONNX file.

### 8. Deploy

Copy **both** files; the `.onnx` alone looks complete but fails to load.

```powershell
Copy-Item .\hey_veda\hey_veda.onnx      ..\data\models\openwakeword\ -Force
Copy-Item .\hey_veda\hey_veda.onnx.data ..\data\models\openwakeword\ -Force
```

Because `data/` is git-ignored, the model is **not in the repository**. A fresh clone or a Pi has
nothing to load until these two files are copied there, and with `wake_engine: openwakeword` voice then
stays disabled (it fails closed; it never falls back to listening continuously).

### 9. Verify and tune the threshold

Stop the Veda server first (only one program can hold the microphone), then:

```powershell
cd ..
python scripts\wake_test.py            # 40 s; use --seconds 90 --threshold 0.6 to experiment
```

Say "Hey Veda" several times, then ordinary sentences (include "hello there" and "okay"), then stay
quiet. `TRIGGER` lines mark scores at or above the threshold. Set `wake_threshold` in `config/audio.yaml`
(0 to 1; higher means fewer false triggers and more misses). Restart `veda` and use it normally.

---

## 8. How long it takes, and how big

**Measured** on this machine, first run (2,000 + 400 positive and 4,000 + 800 negative clips, no 17 GB file;
from design doc §4.8.2):

| Phase | Time |
|---|---|
| Generate 7,200 clips (Piper) | about 5.5 min |
| Download MIT room impulse responses | about 4.5 min |
| Augment and extract features (`--augment_clips`) | about 2 min 14 s |
| Train (`--train_model`: 10,000 steps + two 1,000-step tuning passes, small batches) | about 5 min 30 s |
| **Compute total** | **under 20 min** |

**Estimated** for the retrain (not yet measured): download of the 17.3 GB file 15 to 60 minutes depending
on your connection; training roughly 30 to 90 minutes, because each step now reads about 1,024 background
examples instead of about 100 and is limited by disk reads; generating 10,000+ positive clips adds about
20 to 60 minutes. Realistic total: about 1 to 2.5 hours, mostly download and clip generation.

**Model size:** about 215 KB before and after (see [Summary](#1-summary)).

---

## 9. Using more than one wake phrase

Only `"hey veda"` is trained. To add a phrase (for example "okay veda"):

- Easiest: add it to `target_phrase` in the config and retrain one model that detects either. The
  runtime then needs no change, but a single score covers both phrases.
- Or train a second model and extend `tpa/wake_word/openwakeword_engine.py` to load more than one (the
  engine currently checks a single model name, `scores.get(self._model_name)`).

---

## 10. Measured quality of the current model

Tested on 2026-10-03 against synthetic speech (two Windows SAPI voices), through the real engine:

| Input | Max score | Result at threshold 0.5 |
|---|---|---|
| "Hey Veda" (voice 1 / voice 2) | 0.001 / 0.772 | missed on one voice, hit on the other |
| "Hey Vedha" | 0.827 / 0.004 | inconsistent |
| "Hey Veda what is the weather" (no pause) | 0.002 / 0.002 | missed |
| **"Hello there"** | **0.847** / 0.239 | **false trigger** |
| **"subscribe to our channel"** | **0.616** / 0.299 | **false trigger** |
| "What are my tasks today", "Hey Siri", "Okay Google" | about 0.001 to 0.002 | correctly ignored |
| Silence, white noise | 0.001 | correctly ignored |

Earlier tests during training (design doc §4.8.2) gave about 50% recall and a false trigger on "turn on
the lights". No threshold separates the hits from the false triggers on this data. Note that "hello
there" is already in the training negatives, yet still scores 0.85, a sign the model is under-trained on
negatives. That is what the 17 GB file and your own recordings address. Synthetic voices are not your
voice, so verify with `scripts/wake_test.py` before drawing conclusions.

---

## 11. Known issues and why `run_train.py` exists

`openwakeword.train` is developed on Linux. Running it on Windows needs the following, all applied as
patches inside `run_train.py` (the installed package is never edited):

1. `acoustics` imports `scipy.special.sph_harm`, removed in current scipy. A shim maps it to `sph_harm_y`.
2. `torchaudio.load` needs `torchcodec` and FFmpeg. It is replaced by a `soundfile`-based loader.
3. Windows cannot delete a memory-mapped file that is still open (`trim_mmap`, `compute_features_from_generator`).
   Handles are closed first.
4. `datasets` 4.x also needs `torchcodec`; pin `datasets<4.0`.
5. `rudraml/fma` needs `trust_remote_code=True`; declined, so no background music.
6. DataLoader worker processes cannot pickle openWakeWord's batch generator on Windows (`spawn`);
   `os.cpu_count` is capped at 1, which uses 0 workers (fine for a model this small), and
   `prefetch_factor` is dropped in that case.
7. ONNX export needs `onnxscript` (not a declared dependency).
8. The exporter writes weights to a separate `hey_veda.onnx.data` file. **Copy both files.**

Also: `piper-sample-generator` (the tool openWakeWord's own notebook uses) cannot be installed here
(`piper-phonemize` has no Windows wheel and caps at Python 3.11), which is why `generate_clips.py`
uses `piper-tts` directly.

If you hit a Windows-specific error not listed here, check the training logs in `wakeword_training/`
and design doc §4.8.1 first.

---

## 12. Quick reference

```powershell
# one-time data
curl.exe -L -C - -o openwakeword_features_ACAV100M_2000_hrs_16bit.npy "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy"

# train
python generate_clips.py --output-dir .\hey_veda --n-positive-train 10000 --n-positive-test 2000 --n-negative-train 4000 --n-negative-test 800
python run_train.py --training_config hey_veda_config.yaml --augment_clips --overwrite
python run_train.py --training_config hey_veda_config.yaml --train_model

# deploy (both files!)
Copy-Item .\hey_veda\hey_veda.onnx*  ..\data\models\openwakeword\ -Force

# verify (server stopped)
cd ..; python scripts\wake_test.py
```
