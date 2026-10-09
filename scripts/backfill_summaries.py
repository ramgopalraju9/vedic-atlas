"""Rebuild the missing conversation summaries (see src/service/conversation/backfill.py for why).

    python scripts/backfill_summaries.py            # dry run: counts only, writes nothing
    python scripts/backfill_summaries.py --apply    # back up data/veda.db, then write the summaries

The backup is a consistent SQLite copy (safe while the server is running) saved next to the database as
veda.db.bak-<time>-memory. The local model summarises each stretch of about 12 turns; expect a few seconds per summary.
Nothing is printed from the conversations themselves, only counts.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import ensure_dirs, load_full_config  # noqa: E402
from core.constants import DATA_DIR, PROJECT_ROOT  # noqa: E402
from service.conversation.backfill import SummaryBackfill  # noqa: E402
from service.memory.memory_indexer import MemoryIndexer  # noqa: E402
from tpa.inference.embedding_adapter import OnnxEmbeddingProvider  # noqa: E402
from tpa.inference.factory import build_inference_client  # noqa: E402
from tpa.persistence.migrations import init_tables  # noqa: E402
from tpa.persistence.repositories.conversation_repository import ConversationRepository  # noqa: E402
from tpa.persistence.vector_store import SqliteVectorStore  # noqa: E402


def backup_database() -> Path:
    source = DATA_DIR / "veda.db"
    target = DATA_DIR / f"veda.db.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}-memory"
    src, dst = sqlite3.connect(f"file:{source}?mode=ro", uri=True), sqlite3.connect(target)
    try:
        src.backup(dst)
        for table in ("conversation_turns", "conversation_summaries", "memory_vectors"):
            a = src.execute(f"select count(*) from {table}").fetchone()[0]
            b = dst.execute(f"select count(*) from {table}").fetchone()[0]
            if a != b:
                raise RuntimeError(f"backup check failed for {table}: {a} rows vs {b}")
    finally:
        src.close()
        dst.close()
    return target


def build_client(cfg):
    icfg = cfg.inference
    model_path = Path(icfg.model_path)
    if not model_path.is_absolute():
        model_path = PROJECT_ROOT / model_path
    return build_inference_client(
        backend=icfg.backend, ollama_host=icfg.ollama_host, model_alias=icfg.model_alias,
        llama_cpp_model_path=str(model_path), n_ctx=icfg.n_ctx, n_threads=icfg.n_threads, num_batch=icfg.num_batch,
        num_predict=icfg.num_predict, temperature=icfg.temperature, keep_alive=icfg.keep_alive, think=icfg.think,
        timeout=icfg.timeout, prompt_cache_mb=icfg.prompt_cache_mb, model_lock=None,
    )


async def main(apply: bool) -> int:
    cfg = load_full_config()
    ensure_dirs()
    init_tables()
    repo = ConversationRepository()
    if not apply:
        plan = SummaryBackfill(repo, client=None).plan()   # type: ignore[arg-type]
        print(f"dry run: {len(plan)} session(s) without a summary, {sum(len(c) for _, c in plan)} summaries to write "
              f"({sum(len(t) for _, chunks in plan for t in chunks)} turns). Run with --apply to write them.")
        return 0

    print(f"backup saved: {backup_database().name}")
    ecfg = cfg.embedding
    indexer = None
    if ecfg.enabled:
        path = Path(ecfg.model_path or "data/bge-small-en-v1.5")
        provider = OnnxEmbeddingProvider(model_dir=path if path.is_absolute() else PROJECT_ROOT / path, model_id=ecfg.model_id)
        memory = MemoryIndexer(SqliteVectorStore(), provider)
        indexer = lambda sid, text: memory.index("summary", str(sid), text)   # noqa: E731
    client = build_client(cfg)
    report = await SummaryBackfill(
        repo, client, indexer=indexer, model=cfg.inference.model_alias, num_predict=160,
    ).run(apply=True)
    print(f"done: {report.summaries_written} summaries written for {report.sessions_written}/{report.sessions_planned} session(s)"
          f"{'; failed: ' + ', '.join(report.sessions_failed) if report.sessions_failed else ''}")
    return 1 if report.sessions_failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="back up the database and write the summaries (default: dry run)")
    sys.exit(asyncio.run(main(ap.parse_args().apply)))
