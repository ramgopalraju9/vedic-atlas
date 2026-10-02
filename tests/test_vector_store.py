import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tpa.persistence.session import Base
from tpa.persistence.models import import memory_vector  # noqa: F401 (register table)
from tpa.persistence.vector_store import SqliteVectorStore
from domain.value_objects.embedding import Embedding
from service.memory.semantic_recall import SemanticRecall


def _store():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)
    return SqliteVectorStore(session_factory=factory)


class _FakeEmbedding:
    model_id = "fake-v1"

    def __init__(self, table):
        self._table = table  # text -> vector

    async def embed(self, self_text: str) -> Embedding:
        return Embedding(vector=self._table[self_text], model_id=self.model_id)


def test_search_ranks_by_cosine_and_respects_model_id():
    store = _store()
    store.upsert(source="fact", ref_id="1", model_id="m1", vector=(1.0, 0.0), text="east")
    store.upsert(source="fact", ref_id="2", model_id="m1", vector=(0.0, 1.0), text="north")
    store.upsert(source="fact", ref_id="3", model_id="OTHER", vector=(1.0, 0.0), text="wrong-model")

    hits = store.search(vector=(1.0, 0.0), model_id="m1", top_k=2)
    assert [h.text for h in hits] == ["east", "north"]  # east is the closest
    assert all(h.text != "wrong-model" for h in hits)  # other model excluded


def test_upsert_replaces_and_clear_removes_source():
    store = _store()
    store.upsert(source="fact", ref_id="1", model_id="m1", vector=(1.0, 0.0), text="v1")
    store.upsert(source="fact", ref_id="1", model_id="m1", vector=(0.0, 1.0), text="v2")
    hits = store.search(vector=(0.0, 1.0), model_id="m1", top_k=5)
    assert len(hits) == 1 and hits[0].text == "v2"  # replaced, not duplicated

    store.clear("fact")
    assert store.search(vector=(0.0, 1.0), model_id="m1", top_k=5) == []


def test_semantic_recall_min_score_filter():
    store = _store()
    store.upsert(source="fact", ref_id="1", model_id="fake-v1", vector=(1.0, 0.0), text="relevant")
    store.upsert(source="fact", ref_id="2", model_id="fake-v1", vector=(0.0, 1.0), text="orthogonal")

    emb = _FakeEmbedding({"query": (1.0, 0.0)})
    recall = SemanticRecall(store, emb, top_k=5, min_score=0.5, sources=("fact",))
    hits = asyncio.run(recall.recall("query"))
    assert [h.text for h in hits] == ["relevant"]  # orthogonal (score 0) filtered out


def test_recall_disabled_without_embedding():
    recall = SemanticRecall(_store(), None)
    assert recall.enabled is False
    assert asyncio.run(recall.recall("anything")) == []