"""Agent C: Source Evaluation. Transparent heuristic assessment of relevance, quality and limitations.

These are triage indicators computed from explicit rules (source type, dates, wording cues, duplication).
They are NOT calibrated probabilities of truth, and a reputable publisher does not make every claim correct.
"""
from __future__ import annotations

import logging
import re

from src.agents.base import Agent, RunContext
from src.analysis.text import INJECTION_PATTERNS, OPINION_CUES, STAT_CUES, WEAKNESS_CUES
from src.evidence.store import EvidenceStore
from src.models.evidence import EvidenceQuality, Freshness, Source, SourceAssessment, SourceType
from src.orchestration.state import ResearchState
from src.retrieval.bm25 import BM25Index

logger = logging.getLogger(__name__)
BASE_QUALITY = {
    SourceType.ACADEMIC_PAPER: EvidenceQuality.HIGH, SourceType.OFFICIAL: EvidenceQuality.HIGH,
    SourceType.PREPRINT: EvidenceQuality.MEDIUM, SourceType.ANALYST: EvidenceQuality.MEDIUM,
    SourceType.DATASET: EvidenceQuality.MEDIUM, SourceType.DOCUMENTATION: EvidenceQuality.MEDIUM,
    SourceType.USER_DOCUMENT: EvidenceQuality.MEDIUM, SourceType.NEWS: EvidenceQuality.MEDIUM,
    SourceType.COMPANY: EvidenceQuality.MEDIUM, SourceType.BLOG: EvidenceQuality.LOW,
    SourceType.OPINION: EvidenceQuality.LOW, SourceType.OTHER: EvidenceQuality.LOW,
}
PRIMARY_TYPES = {SourceType.ACADEMIC_PAPER, SourceType.PREPRINT, SourceType.OFFICIAL, SourceType.DATASET,
                 SourceType.DOCUMENTATION, SourceType.COMPANY, SourceType.USER_DOCUMENT}
LEVELS = [EvidenceQuality.LOW, EvidenceQuality.MEDIUM, EvidenceQuality.HIGH]
SECONDARY_CUES = re.compile(r"\b(according to|the company says|the company said|reported|told reporters|said in a "
                            r"statement|announced)\b", re.I)
PROMO_CUES = re.compile(r"\b(our platform|our customers|we deployed|trusted by|leading provider|we plan|our)\b", re.I)
METHOD_CUES = re.compile(r"\b(we study|we find|we compare|survey of|sample|randomi[sz]ed|benchmark|field experiment|"
                         r"data on|dataset|methodology|n\s?=)\b", re.I)


def downgrade(quality: EvidenceQuality, steps: int = 1) -> EvidenceQuality:
    if quality == EvidenceQuality.UNKNOWN:
        return quality
    return LEVELS[max(0, LEVELS.index(quality) - steps)]


def cap(quality: EvidenceQuality, ceiling: EvidenceQuality) -> EvidenceQuality:
    if quality == EvidenceQuality.UNKNOWN:
        return quality
    return LEVELS[min(LEVELS.index(quality), LEVELS.index(ceiling))]


def assess_source(source: Source, relevance: float, today, earlier_related: bool) -> SourceAssessment:
    text = source.content
    flags, limitations, authority = [], list(source.limitations), []
    quality = BASE_QUALITY.get(source.source_type, EvidenceQuality.LOW)
    is_primary = source.source_type in PRIMARY_TYPES

    if source.source_type == SourceType.ACADEMIC_PAPER:
        authority.append("scholarly venue")
    if source.source_type == SourceType.OFFICIAL:
        authority.append("official / regulatory publisher")
    if source.authors:
        authority.append(f"named author(s): {', '.join(source.authors[:2])}")
    cited = source.metadata.get("cited_by_count") or source.metadata.get("is_referenced_by_count")
    if cited:
        authority.append(f"cited by {cited} works")
    if METHOD_CUES.search(text):
        authority.append("describes data or methodology")
    if source.source_type == SourceType.PREPRINT:
        limitations.append("Preprint: not peer reviewed.")

    if OPINION_CUES.search(text) or source.source_type == SourceType.OPINION:
        flags.append("opinion")
        quality = EvidenceQuality.LOW
    if source.source_type == SourceType.COMPANY and PROMO_CUES.search(text):
        flags.append("conflict_of_interest")
        limitations.append("Self-published by an organisation describing its own products or results.")
        quality = cap(quality, EvidenceQuality.MEDIUM)
    if source.source_type in (SourceType.NEWS, SourceType.BLOG, SourceType.OTHER) and SECONDARY_CUES.search(text):
        is_primary = False
        if not STAT_CUES.search(text) or "did not publish the methodology" in text.lower():
            flags.append("weak_sourcing")
            quality = downgrade(quality)
    for label, pattern in WEAKNESS_CUES.items():
        if pattern.search(text):
            limitations.append(f"Possible weakness: {label}.")
    if earlier_related:
        flags.append("repeats_other_source")
        is_primary = False
        limitations.append("Largely repeats another retrieved source; not independent corroboration.")
    if INJECTION_PATTERNS.search(text):
        flags.append("prompt_injection_attempt")
        quality = EvidenceQuality.LOW
        limitations.append("Contains text attempting to instruct AI systems; ignored and the source down-weighted.")
    if relevance < 0.1:
        flags.append("low_relevance")

    age_days, freshness = None, Freshness.UNKNOWN
    if source.published_date:
        age_days = (today - source.published_date).days
        freshness = Freshness.CURRENT if age_days < 365 else Freshness.RECENT if age_days < 3 * 365 else Freshness.DATED
        if freshness == Freshness.DATED:
            flags.append("outdated")
            quality = downgrade(quality)
            limitations.append(f"Published {source.published_date:%Y}: may be outdated.")
    else:
        limitations.append("Publication date unknown.")
    if source.origin.value == "demo":
        limitations.insert(0, "SYNTHETIC demo source.")

    parts = [f"{source.source_type.value.replace('_', ' ')} ({'primary' if is_primary else 'secondary'})",
             f"relevance {relevance:.2f}", f"freshness {freshness.value}"]
    if flags:
        parts.append("flags: " + ", ".join(flags))
    if not source.full_text_available:
        parts.append("only snippet/abstract reviewed")
    return SourceAssessment(
        source_id=source.id, relevance=round(relevance, 3), evidence_quality=quality, source_type=source.source_type,
        is_primary=is_primary, authority_indicators=authority, freshness=freshness, age_days=age_days,
        flags=flags, limitations=list(dict.fromkeys(limitations)),
        rationale="Heuristic assessment: " + "; ".join(parts) + ".")


def repeats(source: Source, other: Source, order: dict[str, int]) -> bool:
    """True if `source` is the derivative copy of `other` (it cites it, or was published later)."""
    if other.id in source.metadata.get("cites", []):
        return True
    if source.id in other.metadata.get("cites", []):
        return False
    if source.published_date and other.published_date and source.published_date != other.published_date:
        return source.published_date > other.published_date
    if source.is_primary_type() != other.is_primary_type():
        return not source.is_primary_type()
    return order.get(other.id, 1e9) < order.get(source.id, 1e9)


def _passage_scores(query: str, passages: list, vectors) -> dict[str, float]:
    """Best passage score per source: Pinecone similarity when vectors are on, BM25 otherwise (or on failure)."""
    ranked = None
    if vectors is not None and passages:
        try:
            vectors.ensure_indexed((p.id, p.text, {"kind": "passage", "source_id": p.source_id}) for p in passages)
            by_id = {p.id: p for p in passages}
            ranked = [(by_id[pid], score) for pid, score in vectors.search(query, k=len(passages), kind="passage")
                      if pid in by_id]
        except Exception:
            logger.warning("Semantic search failed for source relevance; using keyword search.", exc_info=True)
    if ranked is None:
        ranked = BM25Index(passages, lambda p: p.text).search(query, k=len(passages) or 1)
    scores: dict[str, float] = {}
    for passage, score in ranked:
        scores[passage.source_id] = max(scores.get(passage.source_id, 0.0), score)
    return scores


class SourceEvaluatorAgent(Agent):
    name = "source_evaluator"
    title = "Source Evaluation"
    responsibility = "Assesses relevance, authority, primary vs secondary status, freshness, bias and duplication."

    def run(self, state: ResearchState, ctx: RunContext) -> dict:
        sources = state.get("sources", [])
        store = EvidenceStore(sources, state.get("passages", []))
        plan = state["plan"]
        query = " ".join([plan.question] + [s.text for s in plan.subquestions])
        scores = _passage_scores(query, store.all_passages(), ctx.providers.vectors)
        top = max(scores.values(), default=1.0) or 1.0

        assessments = dict(state.get("assessments", {}))
        by_id = {s.id: s for s in sources}
        order = {s.id: i for i, s in enumerate(sources)}
        for source in sources:
            relevance = min(1.0, scores.get(source.id, 0.0) / top)
            earlier_related = any(repeats(source, by_id[r], order) for r in source.related_ids if r in by_id)
            assessments[source.id] = assess_source(source, relevance, ctx.today, earlier_related)
        flagged = sum(1 for a in assessments.values() if a.flags)
        ctx.event(self.name, "completed", f"Assessed {len(assessments)} sources; {flagged} carry quality flags.",
                  state.get("iteration", 0), assessed=len(assessments), flagged=flagged,
                  primary=sum(a.is_primary for a in assessments.values()))
        return {"assessments": assessments}
