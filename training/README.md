# Router training data

Everything needed to turn labelled examples into the files a fine-tuning run reads. The router is the control decode (which tool, or
chat/ask/refuse); chat, narration and summaries stay on the base model.

```
labeling_guide.md   what the right decision is (single source of truth; [OPEN] items are undecided)
sample.py           one example: user, ACTIVE line, earlier exchanges, clock, label, group, status
render.py           builds the EXACT serving text with the real PromptComposer + the model's chat template
validate.py         label checks, including the serving policy (dispatch_policy.resolve) as the last word
build.py            validate -> de-duplicate -> keep the evaluation sets out -> render -> split -> report
seeds.py            real questions from the database, scrubbed, as seeds (utterances only, git-ignored)
make_pilot.py       a hand-checked starter batch that exercises every category and tool
datasets/           samples and build output (synthetic data, safe to commit)
data/               seeds and the private-name list (from real conversations; git-ignored)
```

```bash
python -m training.seeds                     # real questions -> training/data/seeds.jsonl (no replies, names/addresses replaced)
python -m training.make_pilot                # -> training/datasets/pilot.jsonl
python -m training.build --in training/datasets/pilot.jsonl --out training/datasets/build_pilot --exclude-provisional
```

Output: `train.jsonl` / `val.jsonl` with `{"prompt", "completion", ...}` per line and `report.json`.

## What a training example is
`prompt` is what the model reads at serving time, byte for byte:
`<|im_start|>system\n{persona + tools + rules}<|im_end|>\n<|im_start|>user\n{ACTIVE/RECENT/TODAY/USER}\n/no_think<|im_end|>\n<|im_start|>assistant\n`
and `completion` is the compact JSON decision plus `<|im_end|>`. Train with loss on the completion only. Do not use HF `apply_chat_template`
for the target: it inserts an empty `<think>` block that the serving grammar never allows. The 4B, 1.7B and 0.6B GGUF files render this
prompt identically (checked), so one file serves every size.

## Facts measured on this repo
* Prompt mean 2,240 tokens (almost all of it the static rules and tools), completion ~23 tokens.
* The leak check compares every sample to the three evaluation sets (exact text, then cosine ≥ 0.97 with the local bge-small). Writing training data and test
  data in the same voice leaks style even without copies (16 of 79 pilot sentences were word-for-word eval cases), so the **new test set must be written separately**.
* Pilot labels passed the validator without edits, and the validator is strict: it re-runs the serving policy on each label.

## Before a real run
1. Settle the [OPEN] items in `labeling_guide.md`.
2. Generate ~4,000 samples from the guide and the seeds (paraphrase groups of 3–6), run `build`, read the warnings and a random 100.
3. Have a second labeler re-label 10%; read every disagreement.
4. Write and freeze the new test set (400–500) separately, then run `build` again so nothing in it leaks into training.
