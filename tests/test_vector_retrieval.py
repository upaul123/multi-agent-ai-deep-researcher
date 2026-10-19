"""Pinecone vector retrieval, tested offline with a fake embedder and an in-memory index. No key or network needed."""
from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from types import SimpleNamespace

import src.retrieval.vector as vector
from src.agents.qa import answer_question
from src.agents.source_evaluator import _passage_scores
from src.database.repository import Repository
from src.models.evidence import Passage
from src.models.research import ResearchMode
from src.retrieval.vector import EMBED_BATCH, PineconeEmbedder, PineconeStore, RunVectors, open_run_vectors, run_cleanup
from src.search.local import UploadedDocumentProvider
from src.search.registry import build_providers
from src.services.research_service import run_research
from tests.conftest import TODAY

DIM = 256


def _vector(text: str) -> list[float]:
    """Hashed bag-of-words: texts that share words are similar. Stands in for Pinecone Inference."""
    values = [0.0] * DIM
    for word in re.findall(r"[a-z]+", text.lower()):
        values[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = sum(v * v for v in values) ** 0.5 or 1.0
    return [v / norm for v in values]


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def embed(self, texts, input_type):
        self.calls.append((list(texts), input_type))
        return [_vector(t) for t in texts]


class FakeIndex:
    """In-memory Pinecone index stand-in: namespaces, `kind` metadata filter, cosine similarity."""

    def __init__(self, fail=False):
        self.spaces: dict[str, dict] = {}
        self.fail = fail

    def upsert(self, vectors, namespace):
        if self.fail:
            raise RuntimeError("pinecone unreachable")
        space = self.spaces.setdefault(namespace, {})
        for item in vectors:
            space[item["id"]] = (item["values"], item["metadata"])

    def query(self, vector, top_k, namespace, filter=None):
        if self.fail:
            raise RuntimeError("pinecone unreachable")
        wanted = (filter or {}).get("kind", {}).get("$eq")
        matches = [SimpleNamespace(id=vid, score=sum(a * b for a, b in zip(vector, values)))
                   for vid, (values, meta) in self.spaces.get(namespace, {}).items()
                   if wanted is None or meta.get("kind") == wanted]
        matches.sort(key=lambda m: -m.score)
        return SimpleNamespace(matches=matches[:top_k])

    def delete(self, delete_all, namespace):
        self.spaces.pop(namespace, None)


def _passage(pid, source_id, text):
    return Passage(id=pid, source_id=source_id, text=text, location="p. 1", index=0)


def test_vectors_are_written_once_and_ranked_by_similarity():
    index, embedder = FakeIndex(), FakeEmbedder()
    vectors = RunVectors(index, embedder, "run-r1")
    items = [("p1", "Agents reduce support ticket handling time for enterprise teams", {"kind": "passage"}),
             ("p2", "Solar panel efficiency improved in 2025 field trials", {"kind": "passage"})]
    vectors.ensure_indexed(items)
    vectors.ensure_indexed(items)  # already stored by this object: nothing is embedded or written again
    assert len(embedder.calls) == 1 and set(index.spaces["run-r1"]) == {"p1", "p2"}
    hits = vectors.search("How do agents reduce ticket handling time?", k=5, kind="passage")
    assert hits and hits[0][0] == "p1"


def test_search_filters_by_kind_and_drops_weak_matches():
    vectors = RunVectors(FakeIndex(), FakeEmbedder(), "run-r2")
    vectors.ensure_indexed([("p1", "Agents reduce ticket handling time", {"kind": "passage"}),
                            ("s1", "Agents reduce ticket handling time", {"kind": "section"})])
    assert [hid for hid, _ in vectors.search("agents ticket handling time", k=5, kind="passage")] == ["p1"]
    assert vectors.search("quantum neutrino oscillation", k=5, kind="passage") == []
    assert vectors.search("   ", k=5, kind="passage") == []


def test_store_isolates_runs_and_drops_one_namespace():
    index = FakeIndex()
    store = PineconeStore(SimpleNamespace(Index=lambda name: index), "deep-researcher", FakeEmbedder())
    store.for_run("a").ensure_indexed([("p1", "agents ticket time", {"kind": "passage"})])
    store.for_run("b").ensure_indexed([("p2", "agents ticket time", {"kind": "passage"})])
    store.drop_run("a")
    assert "run-a" not in index.spaces and set(index.spaces["run-b"]) == {"p2"}
    store.drop_run("missing")  # a run that never wrote vectors is not an error


def test_embedder_batches_requests_and_reads_values():
    calls = []

    class Item:
        def __init__(self, values):
            self.values = values

    class Inference:
        def embed(self, model, inputs, parameters):
            calls.append((model, len(inputs), parameters["input_type"]))
            return [Item([float(len(t))]) for t in inputs]

    vectors = PineconeEmbedder(SimpleNamespace(inference=Inference()), "llama-text-embed-v2").embed(
        ["a"] * (EMBED_BATCH + 4), "passage")
    assert len(vectors) == EMBED_BATCH + 4 and vectors[0] == [1.0]
    assert calls == [("llama-text-embed-v2", EMBED_BATCH, "passage"), ("llama-text-embed-v2", 4, "passage")]


def test_uploaded_documents_use_vectors_and_fall_back_to_keywords():
    docs = [{"id": "d1", "title": "Ops memo", "kind": "pdf", "filename": "memo.pdf",
             "sections": [("p. 1", "Agents cut ticket handling time by 30 percent.")]},
            {"id": "d2", "title": "Gardening", "kind": "pdf", "filename": "garden.pdf",
             "sections": [("p. 1", "Roses need pruning every spring.")]}]
    semantic = UploadedDocumentProvider(docs, RunVectors(FakeIndex(), FakeEmbedder(), "run-u1"))
    assert [r.doc_ref for r in semantic.search("ticket handling time agents")] == ["d1"]
    broken = UploadedDocumentProvider(docs, RunVectors(FakeIndex(fail=True), FakeEmbedder(), "run-u2"))
    assert [r.doc_ref for r in broken.search("ticket handling time agents")] == ["d1"]  # keyword fallback


def test_source_relevance_uses_vectors_and_falls_back_to_bm25():
    passages = [_passage("s1#p0", "s1", "Agents reduce ticket handling time."),
                _passage("s2#p0", "s2", "Roses need pruning.")]
    query = "ticket handling time"
    keyword = _passage_scores(query, passages, None)
    assert keyword["s1"] > 0 and "s2" not in keyword
    failing = RunVectors(FakeIndex(fail=True), FakeEmbedder(), "run-e1")
    assert _passage_scores(query, passages, failing) == keyword
    semantic = _passage_scores(query, passages, RunVectors(FakeIndex(), FakeEmbedder(), "run-e2"))
    assert semantic["s1"] > 0 and "s2" not in semantic


def test_qa_passages_come_from_vectors_and_fall_back_to_keywords(settings, repo, demo_request):
    state = run_research(demo_request, settings, repo=repo, today=TODAY)
    bundle = repo.load_run(state["run_id"])
    question = bundle.passages[0].text  # the passage's own wording: a clear semantic match
    semantic = answer_question(bundle, question, vectors=RunVectors(FakeIndex(), FakeEmbedder(), "run-q1"))
    assert "semantic similarity" in semantic.how_reached and semantic.citations
    fallback = answer_question(bundle, question, vectors=RunVectors(FakeIndex(fail=True), FakeEmbedder(), "run-q2"))
    assert "keyword relevance" in fallback.how_reached and fallback.citations


def test_demo_and_unconfigured_runs_never_open_vectors(settings):
    keyed = replace(settings, pinecone_api_key="pc-test-key")
    notes: list[str] = []
    assert open_run_vectors(keyed, ResearchMode.DEMO, "r1", notes) is None  # demo mode is always keyword search
    assert open_run_vectors(settings, ResearchMode.LIVE, "r1", notes) is None  # no key configured
    assert notes == [] and run_cleanup(settings) is None
    assert build_providers(keyed, ResearchMode.DEMO, {"web"}, [], run_id="r1").vectors is None


def test_live_run_falls_back_with_a_note_when_pinecone_fails(settings, monkeypatch):
    def unreachable(_settings):
        raise ConnectionError("no route to pinecone")
    monkeypatch.setattr(vector, "vector_store_for", unreachable)
    notes: list[str] = []
    keyed = replace(settings, pinecone_api_key="pc-test-key")
    assert open_run_vectors(keyed, ResearchMode.LIVE, "r1", notes) is None
    assert notes and "keyword search" in notes[0]


def test_deleting_a_run_drops_its_vectors_and_survives_a_failure(settings, demo_request, tmp_path):
    dropped = []
    repo = Repository(tmp_path / "hook.db", on_run_deleted=dropped.append)
    run_id = run_research(demo_request, settings, repo=repo, today=TODAY)["run_id"]
    assert repo.delete_run(run_id) and dropped == [run_id]

    def failing(_run_id):
        raise RuntimeError("pinecone unreachable")
    repo_failing = Repository(tmp_path / "hook2.db", on_run_deleted=failing)
    run_id = run_research(demo_request, settings, repo=repo_failing, today=TODAY)["run_id"]
    assert repo_failing.delete_run(run_id) is True  # the database delete still succeeds
    assert repo_failing.load_run(run_id) is None
