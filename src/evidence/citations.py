"""Citation generation and validation.

Citation validation checks traceability only: that the cited source exists in the research corpus, that
the reference entry matches the source record, that a cited passage is relevant to the sentence citing it,
and that the marker renders. It does NOT establish that the claim is true.
"""
from __future__ import annotations

import re

from src.analysis.text import content_terms, split_sentences
from src.models.evidence import CitationCheck, Origin, ORIGIN_LABELS, Passage, Source

MARKER = re.compile(r"\[(\d{1,3})\]")


def build_references(sources: list[Source]) -> list[dict]:
    """Numbered reference entries in first-use order. URLs are only those recorded on the source."""
    refs = []
    for n, source in enumerate(sources, start=1):
        refs.append({
            "n": n, "source_id": source.id, "label": source.citation_label(), "url": source.url,
            "doc_ref": source.doc_ref, "origin": source.origin.value, "origin_label": ORIGIN_LABELS[source.origin],
            "source_type": source.source_type.value, "synthetic": source.origin == Origin.DEMO,
            "published": source.published_date.isoformat() if source.published_date else None,
            "full_text_reviewed": source.full_text_available,
        })
    return refs


def format_reference(ref: dict) -> str:
    link = f" <{ref['url']}>" if ref.get("url") else (f" {ref['doc_ref']}" if ref.get("doc_ref") else "")
    tags = []
    if ref.get("synthetic"):
        tags.append("SYNTHETIC DEMO SOURCE")
    elif ref.get("origin") == Origin.CACHE.value:
        tags.append("cached result")
    elif ref.get("origin") == Origin.UPLOAD.value:
        tags.append("user-provided document")
    if not ref.get("full_text_reviewed"):
        tags.append("abstract/snippet only")
    return f"[{ref['n']}] {ref['label']}.{link}" + (f" ({'; '.join(tags)})" if tags else "")


def passage_supports(sentence: str, passages: list[Passage], min_shared: int = 3, min_overlap: float = 0.08) -> bool:
    """Lexical relevance check between a citing sentence and the cited source's passages."""
    terms = content_terms(MARKER.sub("", sentence))
    if not terms:
        return True
    for passage in passages:
        shared = terms & content_terms(passage.text)
        if len(shared) >= min_shared or (terms and len(shared) / len(terms) >= min_overlap and len(shared) >= 2):
            return True
    return False


def validate_citations(text: str, references: list[dict], sources: dict[str, Source],
                       passages_by_source: dict[str, list[Passage]]) -> list[CitationCheck]:
    by_n = {ref["n"]: ref for ref in references}
    checks: list[CitationCheck] = []
    seen: set[tuple[str, str]] = set()
    for sentence in split_sentences(text):
        for marker in MARKER.findall(sentence):
            n = int(marker)
            key = (marker, sentence)
            if key in seen:
                continue
            seen.add(key)
            ref = by_n.get(n)
            if ref is None:
                checks.append(CitationCheck(marker=f"[{n}]", source_id=None, exists=False, reference_matches=False,
                                            passage_relevant=False, renderable=False,
                                            note="Marker has no reference entry."))
                continue
            source = sources.get(ref["source_id"])
            exists = source is not None
            matches = exists and ref.get("url") == source.url and ref.get("doc_ref") == source.doc_ref
            relevant = passage_supports(sentence, passages_by_source.get(ref["source_id"], [])) if exists else False
            renderable = bool(ref.get("label"))
            note = "" if relevant else "Cited passage shares little content with the sentence; check this citation."
            checks.append(CitationCheck(marker=f"[{n}]", source_id=ref["source_id"], exists=exists,
                                        reference_matches=matches, passage_relevant=relevant, renderable=renderable,
                                        note=note))
    return checks


def strip_citations(text: str) -> str:
    """Drop numeric citation markers like [1] or [2, 3] from text shown to readers without numbered references."""
    return re.sub(r"[ \t]*\[\d+(?:\s*,\s*\d+)*\]", "", text)


def strip_invalid_markers(text: str, valid_numbers: set[int]) -> tuple[str, int]:
    removed = 0

    def repl(match: re.Match) -> str:
        nonlocal removed
        if int(match.group(1)) in valid_numbers:
            return match.group(0)
        removed += 1
        return ""
    return MARKER.sub(repl, text), removed
