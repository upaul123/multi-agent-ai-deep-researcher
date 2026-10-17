"""Evidence store: normalisation, deduplication, chunking and passage storage for a research run.

Deduplication rules:
- Same canonical URL, DOI/document id, or content hash -> the same source (merged, not re-added).
- Near-identical title from the same publisher -> the same source.
- Near-duplicate *content* from a different publisher (e.g. a news story that reprints a press release)
  -> kept, but linked via `related_ids` and flagged, so the copies are not counted as independent
  corroboration.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.analysis.text import INJECTION_PATTERNS, jaccard, near_duplicate, tokenize
from src.ingestion.chunking import chunk_sections
from src.ingestion.extract import find_references
from src.models.evidence import Origin, Passage, Source, SourceType, content_hash, stable_source_id
from src.search.base import SearchResult

TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_src|igshid)")


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    host = parsed.netloc.lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if not TRACKING_PARAMS.match(k)])
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))


def normalize_doi(ref: str | None) -> str | None:
    if not ref:
        return None
    ref = ref.strip()
    ref = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s?)", "", ref, flags=re.I)
    return ref.lower()


def _safe_source_type(value: str) -> SourceType:
    try:
        return SourceType(value)
    except ValueError:
        return SourceType.OTHER


class EvidenceStore:
    def __init__(self, sources: list[Source] | None = None, passages: list[Passage] | None = None):
        self.sources: dict[str, Source] = {s.id: s for s in sources or []}
        self.passages: dict[str, list[Passage]] = {}
        for p in passages or []:
            self.passages.setdefault(p.source_id, []).append(p)

    # ------------------------------------------------------------------ lookups
    def _find_existing(self, url: str | None, doc_ref: str | None, digest: str, title: str,
                       publisher: str | None) -> Source | None:
        for source in self.sources.values():
            if url and source.url and canonical_url(source.url) == url:
                return source
            if doc_ref and source.doc_ref and normalize_doi(source.doc_ref) == doc_ref:
                return source
            if digest and source.content_hash == digest:
                return source
            if publisher and source.publisher == publisher:
                a, b = set(tokenize(title)), set(tokenize(source.title))  # digits kept: "2024 report" != "2025 report"
                if min(len(a), len(b)) >= 4 and jaccard(a, b) >= 0.9:
                    return source
        return None

    # ------------------------------------------------------------------ add
    def add_result(self, result: SearchResult, origin: Origin, subquestion_id: str | None, query: str,
                   iteration: int) -> tuple[Source, bool]:
        """Normalise a search result into a Source. Returns (source, is_new)."""
        url = canonical_url(result.url)
        doc_ref = normalize_doi(result.doc_ref) if result.doc_ref else None
        text = (result.content or result.snippet or "").strip()
        digest = content_hash(text) if len(text) > 80 else ""
        existing = self._find_existing(url, doc_ref, digest, result.title, result.publisher)
        if existing:
            if subquestion_id and subquestion_id not in existing.subquestion_ids:
                existing.subquestion_ids.append(subquestion_id)
            return existing, False

        key = url or (f"doi:{doc_ref}" if doc_ref else f"{result.provider}:{result.title}:{digest}")
        limitations = list(result.limitations)
        if not result.full_text_available and "only" not in " ".join(limitations).lower():
            limitations.append("Only a snippet/abstract was retrieved; the full text was not reviewed.")
        if INJECTION_PATTERNS.search(text):
            limitations.append("Contains instruction-like text aimed at AI systems; treated strictly as data and "
                               "excluded from claim extraction.")
        source = Source(
            id=stable_source_id(key), title=result.title.strip()[:300], url=url, doc_ref=result.doc_ref,
            publisher=result.publisher, authors=result.authors, published_date=result.published_date,
            source_type=_safe_source_type(result.source_type), origin=origin, retrieval_method=result.provider,
            content=text, full_text_available=result.full_text_available, content_hash=digest,
            limitations=limitations, metadata=dict(result.metadata, provider_score=result.score),
            references_found=list(dict.fromkeys(result.metadata.get("references", []) + find_references(text))),
            subquestion_ids=[subquestion_id] if subquestion_id else [], query=query, iteration=iteration)
        self._link_related(source)
        self.sources[source.id] = source
        sections = result.sections or [("full text" if result.full_text_available else "snippet", text)]
        self.passages[source.id] = chunk_sections(source.id, [(loc, t) for loc, t in sections if t.strip()])
        return source, True

    def _link_related(self, new: Source) -> None:
        """Link syndicated/derivative content and explicit references between sources."""
        for other in self.sources.values():
            related = False
            if new.content and other.content and near_duplicate(new.content, other.content, 0.45):
                related = True
            refs_new = {normalize_doi(r) or canonical_url(r) for r in new.references_found}
            refs_other = {normalize_doi(r) or canonical_url(r) for r in other.references_found}
            if (other.url and canonical_url(other.url) in refs_new) or (other.doc_ref and normalize_doi(other.doc_ref) in refs_new):
                new.metadata.setdefault("cites", []).append(other.id)
                related = related or near_duplicate(new.content, other.content, 0.3)
            if (new.url and new.url in refs_other) or (new.doc_ref and normalize_doi(new.doc_ref) in refs_other):
                other.metadata.setdefault("cites", []).append(new.id)
            if related:
                new.related_ids.append(other.id)
                other.related_ids.append(new.id)

    def replace_content(self, source_id: str, full_text: str) -> None:
        """Swap a snippet for fetched full text (web pages)."""
        source = self.sources[source_id]
        source.content, source.full_text_available = full_text, True
        source.limitations = [l for l in source.limitations if "snippet" not in l.lower()]
        source.references_found = list(dict.fromkeys(source.references_found + find_references(full_text)))
        if INJECTION_PATTERNS.search(full_text) and not any("instruction-like" in l for l in source.limitations):
            source.limitations.append("Contains instruction-like text aimed at AI systems; treated strictly as data.")
        self.passages[source_id] = chunk_sections(source_id, [("full text", full_text)])

    # ------------------------------------------------------------------ passages and independence
    def all_passages(self) -> list[Passage]:
        return [p for ps in self.passages.values() for p in ps]

    def independent_groups(self, source_ids: list[str]) -> list[set[str]]:
        """Group sources that repeat the same original (related_ids), so corroboration isn't double counted."""
        groups: list[set[str]] = []
        for sid in source_ids:
            source = self.sources.get(sid)
            linked = {sid} | (set(source.related_ids) if source else set())
            for group in groups:
                if group & linked:
                    group |= linked
                    break
            else:
                groups.append(set(linked))
        return groups

    def independent_count(self, source_ids: list[str]) -> int:
        return len(self.independent_groups(list(dict.fromkeys(source_ids))))

    def as_lists(self) -> tuple[list[Source], list[Passage]]:
        return list(self.sources.values()), self.all_passages()
