"""Claim extraction: deterministic sentence-level extraction, and validation of LLM-proposed claims.

Every claim keeps an exact quote. LLM-proposed claims are accepted only if the quote is found verbatim
(ignoring whitespace/case) in one of the source's passages. This blocks invented quotations.
"""
from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, Field

from src.analysis.text import (CLAIM_CUES, FORECAST_CUES, INJECTION_PATTERNS, OPINION_CUES, STAT_CUES, polarity,
                               split_sentences)
from src.models.evidence import Claim, ClaimType, Passage, Source
from src.models.research import ResearchPlan
from src.retrieval.bm25 import BM25Index
from src.analysis.claim_validation import paraphrase_issues

MAX_CLAIMS_PER_SOURCE = 6


def classify_claim(sentence: str) -> ClaimType:
    if OPINION_CUES.search(sentence):
        return ClaimType.OPINION
    if FORECAST_CUES.search(sentence):
        return ClaimType.FORECAST
    if STAT_CUES.search(sentence):
        return ClaimType.STATISTIC
    return ClaimType.FACT


def claim_id(source_id: str, quote: str) -> str:
    return "clm_" + hashlib.sha1(f"{source_id}|{quote.lower()}".encode()).hexdigest()[:12]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


PERSPECTIVE_VOCAB = {
    "barrier": "barrier risk limitation reliability fail failure constraint regulation governance oversight audit "
               "integration cost error liability concern",
    "driver": "productivity benefit gain improve reduce cost faster growth increase capability",
    "forecast": "will expect forecast predict by 2028 2030 future replace estimate",
    "current": "survey adoption percent deploy production use today report",
    "implication": "pricing business model revenue seat outcome usage market",
    "contradict": "overstated not evidence limited fail rather than criticism",
    "evaluation": "evaluation metric benchmark faithfulness measure judge",
    "techniques": "improve accuracy recall retrieval hybrid graph rerank",
    "risk": "risk concern constraint regulation compliance liability",
}


def _vocab(perspective: str) -> str:
    return " ".join(v for k, v in PERSPECTIVE_VOCAB.items() if k in perspective)


class SubquestionMatcher:
    def __init__(self, plan: ResearchPlan):
        self.subquestions = plan.subquestions
        self.index = BM25Index(plan.subquestions,
                               lambda s: f"{s.text} {' '.join(s.queries)} {s.perspective} {_vocab(s.perspective)}")

    def best(self, text: str, fallback: list[str]) -> str | None:
        hits = self.index.search(text, k=1)
        if hits and hits[0][1] > 0.5:
            return hits[0][0].id
        return fallback[0] if fallback else (self.subquestions[0].id if self.subquestions else None)


def heuristic_claims(source: Source, passages: list[Passage], plan: ResearchPlan,
                     matcher: SubquestionMatcher) -> list[Claim]:
    """Sentences that assert something checkable (numbers, findings, forecasts), ranked by relevance."""
    candidates: list[tuple[Passage, str]] = []
    for passage in passages:
        for sentence in split_sentences(passage.text):
            if not 40 <= len(sentence) <= 360 or sentence.endswith("?"):
                continue
            if INJECTION_PATTERNS.search(sentence):
                continue  # instruction-like text is never treated as a claim
            if CLAIM_CUES.search(sentence) or STAT_CUES.search(sentence) or OPINION_CUES.search(sentence):
                candidates.append((passage, sentence))
    if not candidates:
        return []
    query = plan.question + " " + " ".join(s.text + " " + " ".join(s.queries) for s in plan.subquestions)
    scores = {id(c): score for c, score in BM25Index(candidates, lambda c: c[1]).search(query, k=len(candidates))}
    order = {id(c): i for i, c in enumerate(candidates)}
    chosen = sorted(candidates, key=lambda c: (-scores.get(id(c), 0.0), order[id(c)]))[:MAX_CLAIMS_PER_SOURCE]
    claims = []
    for passage, sentence in sorted(chosen, key=lambda c: order[id(c)]):
        claims.append(Claim(
            id=claim_id(source.id, sentence), text=sentence, quote=sentence, claim_type=classify_claim(sentence),
            polarity=polarity(sentence), source_id=source.id, passage_id=passage.id, location=passage.location,
            subquestion_id=matcher.best(sentence, source.subquestion_ids), extracted_by="heuristic"))
    return claims


class ClaimDraft(BaseModel):
    quote: str = Field(description="Exact sentence copied from the source text")
    claim: str = Field(description="The claim restated concisely")
    claim_type: str = Field(description="fact | statistic | forecast | opinion")
    sceptical: bool = Field(False, description="True if the claim doubts, negates or limits a premise")


class ClaimDraftList(BaseModel):
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=10)


def validate_llm_claims(drafts: ClaimDraftList, source: Source, passages: list[Passage],
                        matcher: SubquestionMatcher) -> tuple[list[Claim], int]:
    """Validate quoted evidence and paraphrase integrity, preserving original source wording."""
    accepted, rejected = [], 0
    for draft in drafts.claims:
        quote = draft.quote.strip().strip('"“”')
        passage = next((p for p in passages if len(quote) >= 25 and _norm(quote) in _norm(p.text)), None)
        if passage is None or INJECTION_PATTERNS.search(quote):
            rejected += 1
            continue
        if paraphrase_issues(quote, draft.claim.strip() or quote):
            rejected += 1
            continue
        # Keep the original source spelling and whitespace, rather than model-normalized wording.
        match = re.search(r"\s+".join(re.escape(word) for word in quote.split()), passage.text, re.I)
        if match is None:
            rejected += 1
            continue
        quote = match.group(0)
        ctype = classify_claim(quote)
        accepted.append(Claim(
            id=claim_id(source.id, quote), text=quote, quote=quote, claim_type=ctype,
            polarity=polarity(quote), source_id=source.id, passage_id=passage.id,
            location=passage.location, subquestion_id=matcher.best(quote + " " + draft.claim, source.subquestion_ids),
            extracted_by="llm", notes=["Canonical source wording retained; paraphrase integrity checks passed."]))
    return accepted[:MAX_CLAIMS_PER_SOURCE + 2], rejected
