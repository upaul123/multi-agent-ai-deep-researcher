"""Dense (vector) passage retrieval on Pinecone. Live research only: demo mode always uses BM25.

Pinecone holds one vector per passage (or uploaded section), in a namespace per investigation (`run-<run id>`), so one
run's vectors never appear in another run's results. Vectors carry a kind and a small pointer back to the passage;
passage text stays in the app database. If Pinecone cannot be reached, callers fall back to BM25 and say so.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from functools import lru_cache
from typing import Any

from pinecone import Pinecone, ServerlessSpec
from pinecone.errors import NotFoundError

from src.models.research import ResearchMode

logger = logging.getLogger(__name__)

NAMESPACE_PREFIX = "run-"
EMBED_BATCH = 96  # conservative per-request input count for hosted embedding models
MIN_SIMILARITY = 0.3  # cosine similarity below this is no match; to be tuned on real investigations

Item = tuple[str, str, dict[str, Any]]  # (vector id, text to embed, small metadata)


class PineconeEmbedder:
    """Embeddings from Pinecone Inference, so no second provider key is needed."""

    def __init__(self, client, model: str):
        self.client, self.model = client, model

    def embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            result = self.client.inference.embed(model=self.model, inputs=texts[start:start + EMBED_BATCH],
                                                 parameters={"input_type": input_type, "truncate": "END"})
            vectors.extend(list(item.values) for item in result)
        return vectors


class RunVectors:
    """Vectors for one investigation: one namespace, written once per id by this object."""

    def __init__(self, index, embedder: PineconeEmbedder, namespace: str):
        self.index, self.embedder, self.namespace = index, embedder, namespace
        self._stored: set[str] = set()

    def ensure_indexed(self, items: Iterable[Item]) -> None:
        """Embed and store the items this object has not stored yet. Ids are stable, so a rewrite is harmless."""
        fresh = [item for item in items if item[0] not in self._stored]
        if not fresh:
            return
        vectors = self.embedder.embed([text for _, text, _ in fresh], "passage")
        self.index.upsert(vectors=[{"id": vid, "values": vec, "metadata": meta}
                                   for (vid, _, meta), vec in zip(fresh, vectors)], namespace=self.namespace)
        self._stored.update(vid for vid, _, _ in fresh)

    def search(self, query: str, k: int, kind: str) -> list[tuple[str, float]]:
        """(vector id, similarity) of the closest items of one kind, most similar first."""
        if not query.strip():
            return []
        [vector] = self.embedder.embed([query], "query")
        response = self.index.query(vector=vector, top_k=k, namespace=self.namespace,
                                    filter={"kind": {"$eq": kind}})
        return [(match.id, round(match.score, 4)) for match in response.matches if match.score >= MIN_SIMILARITY]


class PineconeStore:
    """One Pinecone index shared by all runs. Isolation comes from the per-run namespaces."""

    def __init__(self, client, index_name: str, embedder: PineconeEmbedder):
        self.index = client.Index(index_name)
        self.embedder = embedder

    def for_run(self, run_id: str) -> RunVectors:
        return RunVectors(self.index, self.embedder, NAMESPACE_PREFIX + run_id)

    def drop_run(self, run_id: str) -> None:
        """Delete a run's vectors. A run that never wrote any is not an error."""
        try:
            self.index.delete(delete_all=True, namespace=NAMESPACE_PREFIX + run_id)
        except NotFoundError:
            pass


@lru_cache(maxsize=2)
def _store(api_key: str, index_name: str, cloud: str, region: str, model: str) -> PineconeStore:
    client = Pinecone(api_key=api_key)
    if not client.has_index(index_name):
        dimension = client.inference.get_model(model=model).default_dimension  # matches the embedding model exactly
        client.create_index(name=index_name, dimension=dimension, metric="cosine",
                            spec=ServerlessSpec(cloud=cloud, region=region))
    return PineconeStore(client, index_name, PineconeEmbedder(client, model))


def vector_store_for(settings) -> PineconeStore | None:
    """The shared store, or None without PINECONE_API_KEY. Raises if Pinecone is unreachable."""
    if not settings.pinecone_api_key:
        return None
    return _store(settings.pinecone_api_key, settings.pinecone_index, settings.pinecone_cloud,
                  settings.pinecone_region, settings.pinecone_embed_model)


def open_run_vectors(settings, mode: str, run_id: str | None, notes: list[str]) -> RunVectors | None:
    """Vectors for a live investigation. None (keyword search) for demo runs, without a key, or when Pinecone fails."""
    if mode != ResearchMode.LIVE or not settings.pinecone_api_key or not run_id:
        return None
    try:
        return vector_store_for(settings).for_run(run_id)
    except Exception as exc:  # an unreachable or misconfigured Pinecone must not stop the research
        logger.warning("Pinecone unavailable; using keyword search.", exc_info=True)
        notes.append(f"Semantic search unavailable ({type(exc).__name__}); passages are ranked by keyword search.")
        return None


def run_cleanup(settings) -> Callable[[str], None] | None:
    """Deletes a deleted run's vectors. The store is created lazily, so a Pinecone outage cannot block startup."""
    if not settings.pinecone_api_key:
        return None
    return lambda run_id: vector_store_for(settings).drop_run(run_id)
