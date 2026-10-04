"""Semantic memory: the ONNX embedding provider and the "what is my fav sweet?" recall it enables.

Regression: a user said their favourite sweet; two sessions later Veda could not recall it. The statement was
stored (in a conversation summary) but only the 3 newest summaries reach the prompt, and the meaning-based
recall that should find older ones was off (the embedding model was never staged, and the adapter passed a
folder path where fastembed needs a model name).

Tests marked "real model" need data/bge-small-en-v1.5/{model.onnx,tokenizer.json} (git-ignored; scripts/setup_pi.sh
fetches them) and are skipped when it is absent. Run: pytest tests/test_embedding_provider.py
"""

import asyncio
import math
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import server
from core.config import load_full_config
from service.memory.memory_indexer import MemoryIndexer
from service.memory.semantic_recall import SemanticRecall
from tpa.inference.embedding_adapter import OnnxEmbeddingProvider
from tpa.persistence.models import memory_vector  # noqa: F401 (register table)
from tpa.persistence.session import Base
from tpa.persistence.vector_store import SqliteVectorStore

MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "bge-small-en-v1.5"
HAS_MODEL = (MODEL_DIR / "model.onnx").is_file() and (MODEL_DIR / "tokenizer.json").is_file()
real_model = pytest.mark.skipif(not HAS_MODEL, reason="embedding model not staged in data/bge-small-en-v1.5")


def run(coro):
    return asyncio.run(coro)


# ---- clear errors when the model is not there (no model needed) ----------------------------

def test_missing_model_folder_is_a_clear_error_with_the_fix(tmp_path):
    with pytest.raises(FileNotFoundError) as err:
        OnnxEmbeddingProvider(tmp_path / "nope")
    assert "model.onnx" in str(err.value) and "tokenizer.json" in str(err.value) and "setup_pi.sh" in str(err.value)


def test_model_without_tokenizer_is_rejected(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        OnnxEmbeddingProvider(tmp_path)


def test_server_turns_a_missing_model_into_memory_off_not_a_crash(tmp_path):
    cfg = load_full_config().model_copy(deep=True)
    cfg.embedding.enabled, cfg.embedding.model_path = True, str(tmp_path / "missing")
    assert server._build_embedding(cfg) is None


def test_server_does_not_build_it_when_disabled():
    cfg = load_full_config().model_copy(deep=True)
    cfg.embedding.enabled = False
    assert server._build_embedding(cfg) is None


# ---- real model ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def provider():
    return OnnxEmbeddingProvider(MODEL_DIR)


@real_model
def test_embeddings_are_384_dim_unit_length_and_deterministic(provider):
    a, b = run(provider.embed("hello world")), run(provider.embed("hello world"))
    assert len(a.vector) == 384 and a.vector == b.vector
    assert math.isclose(math.sqrt(sum(x * x for x in a.vector)), 1.0, abs_tol=1e-6)
    assert a.model_id == "BAAI/bge-small-en-v1.5"        # a stable name, not a machine-specific path


@real_model
def test_empty_and_very_long_text_do_not_crash(provider):
    assert len(run(provider.embed("")).vector) == 384
    assert len(run(provider.embed("word " * 5000)).vector) == 384   # truncated to the model's 512-token limit


def _cos(a, b):
    return sum(x * y for x, y in zip(a.vector, b.vector))


@real_model
def test_a_paraphrase_is_closer_than_an_unrelated_sentence(provider):
    fact = run(provider.embed("User said gulab jamun is their favourite sweet."))
    assert _cos(run(provider.embed("which sweets do I like?")), fact) > _cos(run(provider.embed("what is the capital of France?")), fact)


SUMMARIES = {
    "8": "User asked about Raspberry Pi, Veda explained it as a small computer. User complimented on gulab gamun, "
         "Veda acknowledged it as favorite sweet. Outcome: user's sweet preference recorded.",
    "9": "User asked about the weather in Hyderabad; Veda provided the forecast and offered to check the rest of the day.",
    "10": "User asked for the capital of France; Veda answered Paris. User asked for the capital of Japan; Tokyo.",
    "11": "User added a task to bring vegies and get milk home; Veda marked the vegies task as done.",
}


@real_model
@pytest.mark.parametrize("question", [
    "what is my fav sweet?",
    "which sweets do I like?",
    "do you remember what dessert I love",
    "what did I say about gulab jamun?",          # spelled differently from the stored "gamun"
])
def test_the_old_sweet_preference_is_recalled_by_meaning(provider, question):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    store = SqliteVectorStore(session_factory=sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False))
    indexer = MemoryIndexer(store, provider)
    for ref, text in SUMMARIES.items():
        assert run(indexer.index("summary", ref, text))

    recall = SemanticRecall(store, provider, top_k=3, min_score=0.5, sources=("fact", "summary"))
    hits = run(recall.recall(question))
    assert hits, "nothing recalled"
    assert "gulab" in hits[0].text, f"the sweet summary should rank first, got: {[h.text[:40] for h in hits]}"
    assert "gulab" in recall.as_context_block(hits)        # what the model is shown
