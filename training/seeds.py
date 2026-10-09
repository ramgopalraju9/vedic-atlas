"""Export the user's real spoken/typed questions as SEEDS for paraphrase generation. Utterances only; never replies.

    python -m training.seeds            # writes training/data/seeds.jsonl (git-ignored: it comes from real conversations)

What it does: takes the user turns from the database, drops the ones too short to teach anything (<= 2 words), drops the ones
that are also in an evaluation set (they would leak), replaces email addresses and the names listed in
training/data/private_names.txt with made-up ones, and removes duplicates. The output has NO labels: a seed is only the shape of a
real request, which the generator rewrites with synthetic names and details. Nothing here is sent anywhere.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training.build import eval_utterances, norm  # noqa: E402

DB = ROOT / "data" / "veda.db"
OUT = ROOT / "training" / "data" / "seeds.jsonl"
NAMES = ROOT / "training" / "data" / "private_names.txt"
EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(\.[\w-]+)+")
FAKE_NAMES = ["Asha", "Ravi", "Meera", "Karan", "Divya", "Sameer", "Leela", "Naveen", "Tara", "Imran"]
FAKE_DOMAINS = ["example.com", "example.org", "mail.example.net"]


def scrub(text: str, private: list[str], names: dict[str, str]) -> str:
    def fake_email(match: re.Match) -> str:
        key = match.group(0).lower()
        if key not in names:
            names[key] = f"{FAKE_NAMES[len(names) % len(FAKE_NAMES)].lower()}{len(names)}@{FAKE_DOMAINS[len(names) % len(FAKE_DOMAINS)]}"
        return names[key]

    text = EMAIL.sub(fake_email, text)
    for name in private:
        if name.lower() not in names:
            names[name.lower()] = FAKE_NAMES[len(names) % len(FAKE_NAMES)]
        text = re.sub(rf"\b{re.escape(name)}\w*", names[name.lower()], text, flags=re.IGNORECASE)
    return text


def main() -> int:
    private = [n.strip() for n in NAMES.read_text(encoding="utf-8").splitlines() if n.strip()] if NAMES.exists() else []
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = [r[0] for r in db.execute("select content from conversation_turns where role='user' order by id")]
    evals = {norm(e) for e in eval_utterances()}
    seen, seeds, stats = set(), [], {"turns": len(rows), "too_short": 0, "in_eval_sets": 0, "duplicate": 0}
    mapping: dict[str, str] = {}
    for text in rows:
        if len(text.split()) <= 2:
            stats["too_short"] += 1
        elif norm(text) in evals:
            stats["in_eval_sets"] += 1
        elif norm(text) in seen:
            stats["duplicate"] += 1
        else:
            seen.add(norm(text))
            seeds.append(scrub(text, private, mapping))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps({"seed": s}, ensure_ascii=False) + "\n" for s in seeds), encoding="utf-8", newline="\n")
    leftovers = sum(bool(EMAIL.search(s)) and not any(d in s for d in FAKE_DOMAINS) for s in seeds)
    print(f"{stats} -> {len(seeds)} seeds written to {OUT.relative_to(ROOT)}; real-looking addresses left: {leftovers}; "
          f"names scrubbed: {len(private)} (edit training/data/private_names.txt to add more)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
