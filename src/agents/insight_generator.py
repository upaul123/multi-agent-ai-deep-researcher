"""Agent E: Insight Generation. Patterns, trends, hypotheses, implications, risks and opportunities.

Every insight carries an epistemic label: OBSERVED (stated by sources), INFERRED (reasoned from several
observations) or SPECULATIVE (a hypothesis to test). In live mode the LLM may propose insights, but each
one must cite existing claim ids; insights citing unknown ids are discarded.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from pydantic import BaseModel, Field

from src.agents.base import Agent, RunContext
from src.retrieval.bm25 import BM25Index
from src.analysis.text import content_terms, tokenize, truncate
from src.evidence.store import EvidenceStore
from src.models.evidence import (Agreement, Claim, ClaimType, EpistemicLabel, EvidenceQuality, Insight, InsightKind,
                                 MatrixRow)
from src.orchestration.state import ResearchState
from src.services.llm import wrap_untrusted

RISK_CUES = re.compile(r"\b(risks?|concerns?|barriers?|liabilit\w*|exposure|errors?|churn|dependence|constraints?|"
                       r"compliance|regulatory|squeez\w*|compress\w*|lower gross margins?)\b", re.I)
OPPORTUNITY_CUES = re.compile(r"\b(opportunit\w*|demand|capture|priority sectors?|new markets?|subsidi[sz]ed|"
                              r"compute credits|reached .* faster|faster than)\b", re.I)
TREND_STOP = {"found", "find", "show", "report", "use", "company", "percent", "customer", "said", "say", "expect",
              "average", "median", "total", "first", "most", "large", "small", "high", "higher", "lower", "time",
              "result", "data", "number", "share", "part", "point", "case", "study", "survey", "source", "rather",
              "question", "answer", "system", "task", "work", "enterprise", "company", "organisation", "firm",
              "current", "currently", "approach", "method", "model", "per", "across", "end", "year"}


def _id(*parts: str) -> str:
    return "ins_" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def confidence_for(row: MatrixRow) -> tuple[str, str]:
    if row.agreement == Agreement.STRONG and row.evidence_quality == EvidenceQuality.HIGH:
        return "high", f"{row.independent_sources} independent sources agree, including high-quality evidence."
    if row.agreement in (Agreement.STRONG, Agreement.MODERATE):
        return "medium", f"{row.independent_sources} independent sources agree; best evidence quality is {row.evidence_quality.value}."
    if row.agreement == Agreement.SINGLE_SOURCE:
        return "low", "Supported by a single independent source."
    return "low", "Sources disagree; the balance of evidence is unclear."


class InsightDraft(BaseModel):
    kind: str
    label: str
    statement: str
    evidence_claim_ids: list[str] = Field(min_length=1)
    reasoning: str
    alternatives: list[str] = Field(default_factory=list)
    confidence: str
    confidence_explanation: str
    limitations: list[str] = Field(default_factory=list)
    next_investigation: str = ""


class InsightDraftList(BaseModel):
    insights: list[InsightDraft] = Field(default_factory=list, max_length=14)


class InsightGeneratorAgent(Agent):
    name = "insight_generator"
    title = "Insight Generation"
    responsibility = "Connects findings across subquestions into labelled findings, trends, hypotheses, risks and opportunities."
    uses_llm = "optional"

    def run(self, state: ResearchState, ctx: RunContext) -> dict:
        claims = {c.id: c for c in state.get("claims", [])}
        rows = state.get("matrix", [])
        insights = None
        if ctx.llm_enabled and rows:
            insights = self._llm_insights(state, claims, ctx)
            if insights is None:
                ctx.event(self.name, "info", "LLM insights unavailable or failed validation; using deterministic insights.")
        insights = insights or self.heuristic_insights(state)
        counts = defaultdict(int)
        for i in insights:
            counts[i.label.value] += 1
        ctx.event(self.name, "completed", f"{len(insights)} insights: " + ", ".join(f"{v} {k}" for k, v in counts.items()),
                  state.get("iteration", 0), insights=len(insights))
        return {"insights": insights}

    # ------------------------------------------------------------------ deterministic insights
    def heuristic_insights(self, state: ResearchState) -> list[Insight]:
        claims: dict[str, Claim] = {c.id: c for c in state.get("claims", [])}
        rows: list[MatrixRow] = state.get("matrix", [])
        store = EvidenceStore(state.get("sources", []), state.get("passages", []))
        contradictions = state.get("contradictions", [])
        plan = state["plan"]
        synthetic = any(s.is_synthetic for s in store.sources.values())
        base_limits = ["Based on synthetic demo sources: illustrative only."] if synthetic else []
        insights: list[Insight] = []

        # 1) Observed findings: corroborated rows.
        corroborated = sorted([r for r in rows if r.agreement in (Agreement.STRONG, Agreement.MODERATE)
                               and r.claim_type != ClaimType.OPINION],
                              key=lambda r: (-r.independent_sources, r.id))
        for row in corroborated[:5]:
            conf, why = confidence_for(row)
            insights.append(Insight(
                id=_id("finding", row.id), kind=InsightKind.FINDING, label=EpistemicLabel.OBSERVED,
                statement=row.claim, evidence_claim_ids=row.supporting_claim_ids, source_ids=row.supporting_source_ids,
                reasoning=f"Stated by {row.independent_sources} independent sources ({row.assessment})",
                alternatives=["Sources may share an unstated common origin", "Measurements may use different definitions"],
                confidence=conf, confidence_explanation=why, limitations=base_limits + row.weaknesses,
                next_investigation="Look for primary data that measures this directly."))

        # 2) Disagreements, represented fairly (no forced consensus).
        for con in contradictions[:4]:
            a, b = claims.get(con.claim_a_id), claims.get(con.claim_b_id)
            if not (a and b):
                continue
            insights.append(Insight(
                id=_id("disagree", con.id), kind=InsightKind.DISAGREEMENT, label=EpistemicLabel.OBSERVED,
                statement=f"Sources disagree: \"{truncate(a.text, 140)}\" versus \"{truncate(b.text, 140)}\"",
                evidence_claim_ids=[a.id, b.id], source_ids=[a.source_id, b.source_id],
                reasoning=f"{con.nature.title()} contradiction. {con.explanation}",
                alternatives=["The sources may measure different populations, definitions or time periods",
                              "One source may be promotional or speculative"],
                confidence="medium", confidence_explanation="The disagreement itself is directly observable; which side is right is not established.",
                limitations=base_limits, next_investigation="Compare the methodologies and definitions behind each figure or claim."))

        # 3) Trends: distinctive terms/phrases recurring across independent sources and several subquestions.
        topic_terms = content_terms(plan.question)
        relevance = self._claim_relevance(claims, plan)
        relevant = [c for c in claims.values() if relevance.get(c.id, 0) >= 0.15]
        phrase_sources: dict[str, set[str]] = defaultdict(set)
        phrase_subqs: dict[str, set[str]] = defaultdict(set)
        phrase_claims: dict[str, list[str]] = defaultdict(list)
        for claim in relevant:
            words = [w for w in tokenize(claim.text) if not w.isdigit() and len(w) > 2]
            candidates = {w for w in words if w not in TREND_STOP and w not in topic_terms
                          and not any(w.startswith(t[:5]) or t.startswith(w[:5]) for t in topic_terms)}
            candidates |= {f"{a} {b}" for a, b in zip(words, words[1:]) if a not in TREND_STOP and b not in TREND_STOP
                           and not ({a, b} <= topic_terms)}
            for phrase in candidates:
                phrase_sources[phrase].add(claim.source_id)
                phrase_subqs[phrase].add(claim.subquestion_id or "")
                phrase_claims[phrase].append(claim.id)

        def strength(phrase: str) -> int:
            return store.independent_count(sorted(phrase_sources[phrase]))

        def qualifies(phrase: str) -> bool:
            is_phrase = " " in phrase or "-" in phrase
            needed = 2 if is_phrase else 4
            return (strength(phrase) >= needed and len(phrase_subqs[phrase]) >= 2
                    and (is_phrase or len(phrase) >= 7))

        trends: list[str] = []
        for phrase in sorted((p for p in phrase_sources if qualifies(p)), key=lambda p: (-strength(p), -len(p), p)):
            if not any(phrase in t or t in phrase for t in trends):
                trends.append(phrase)
            if len(trends) == 3:
                break
        for term in trends:
            n = strength(term)
            insights.append(Insight(
                id=_id("trend", term), kind=InsightKind.TREND, label=EpistemicLabel.INFERRED,
                statement=f"A recurring theme across {n} independent sources is '{term}', appearing in "
                          f"{len(phrase_subqs[term])} different parts of the question.",
                evidence_claim_ids=phrase_claims[term][:6], source_ids=sorted(phrase_sources[term])[:6],
                reasoning="Inferred from how often the theme recurs across independent sources and subquestions.",
                alternatives=["Frequency may reflect what is easy to write about, not what matters most"],
                confidence="medium" if n >= 4 else "low",
                confidence_explanation="Recurrence is a pattern signal, not proof of importance.",
                limitations=base_limits, next_investigation=f"Quantify how '{term}' affects outcomes with primary data."))

        # 4) Hypotheses from forecasts (speculative by definition).
        forecast_rows = [r for r in rows if r.claim_type == ClaimType.FORECAST
                         and max((relevance.get(cid, 0) for cid in r.supporting_claim_ids), default=0) >= 0.25]
        for row in forecast_rows[:3]:
            contested = row.agreement in (Agreement.CONTESTED, Agreement.MIXED)
            insights.append(Insight(
                id=_id("hyp", row.id), kind=InsightKind.HYPOTHESIS, label=EpistemicLabel.SPECULATIVE,
                statement=f"Hypothesis: {row.claim}",
                evidence_claim_ids=row.supporting_claim_ids + row.contradicting_claim_ids,
                source_ids=row.supporting_source_ids + row.contradicting_source_ids,
                reasoning="A forward-looking claim made by sources; it cannot be verified with present evidence.",
                alternatives=["Adoption could stall on reliability, integration or regulation",
                              "The change may be gradual augmentation rather than replacement"],
                confidence="low",
                confidence_explanation="Forecast; " + ("sources disagree." if contested else
                                                       f"{row.independent_sources} independent source(s)."),
                limitations=base_limits + row.weaknesses,
                next_investigation="Define measurable leading indicators and track them over time."))

        # 5) Risks and opportunities (inferred from claims with risk/opportunity language).
        used = {cid for i in insights for cid in i.evidence_claim_ids}
        for kind, pattern, label in ((InsightKind.RISK, RISK_CUES, "Risk"), (InsightKind.OPPORTUNITY, OPPORTUNITY_CUES,
                                                                              "Opportunity")):
            matches = [c for c in claims.values() if pattern.search(c.text) and c.claim_type != ClaimType.OPINION
                       and relevance.get(c.id, 0) >= 0.35
                       and not (kind == InsightKind.OPPORTUNITY and RISK_CUES.search(c.text))]
            matches.sort(key=lambda c: (c.id in used, -relevance.get(c.id, 0), c.id))
            for claim in matches[:2]:
                source = store.sources.get(claim.source_id)
                insights.append(Insight(
                    id=_id(kind.value, claim.id), kind=kind, label=EpistemicLabel.INFERRED,
                    statement=f"{label}: {claim.text}", evidence_claim_ids=[claim.id], source_ids=[claim.source_id],
                    reasoning=f"Drawn from {source.title if source else 'a source'}; its significance is an interpretation.",
                    alternatives=["Magnitude may differ outside the setting studied"],
                    confidence="low", confidence_explanation="Single-source observation, interpreted for implications.",
                    limitations=base_limits, next_investigation="Seek independent evidence of scale and likelihood."))
        return insights

    @staticmethod
    def _claim_relevance(claims: dict[str, Claim], plan) -> dict[str, float]:
        """BM25 relevance of each claim to the question and its subquestions, normalised to 0..1."""
        query = plan.question + " " + " ".join(s.text for s in plan.subquestions)
        ranked = BM25Index(list(claims.values()), lambda c: c.text).search(query, k=len(claims) or 1)
        top = ranked[0][1] if ranked else 1.0
        return {c.id: score / top for c, score in ranked}

    # ------------------------------------------------------------------ LLM insights
    def _llm_insights(self, state: ResearchState, claims: dict[str, Claim], ctx: RunContext) -> list[Insight] | None:
        rows = state.get("matrix", [])[:30]
        lines = []
        for row in rows:
            lines.append(f"- row {row.id} [{row.agreement.value}, quality {row.evidence_quality.value}, "
                         f"{row.independent_sources} independent]: {truncate(row.claim, 240)} | supporting claims: "
                         f"{', '.join(row.supporting_claim_ids[:4])} | contradicting: {', '.join(row.contradicting_claim_ids[:3]) or 'none'}")
        prompt = ("Research question:\n" + wrap_untrusted("research_question", state['plan'].question)
                  + "\nEvidence matrix (claim ids in brackets are the only "
                  "valid evidence references):\n" + wrap_untrusted("evidence_matrix", "\n".join(lines)) +
                  "\n\nGenerate 6-10 insights. kind is one of finding, trend, hypothesis, implication, risk, opportunity, "
                  "disagreement. label is observed (stated directly by sources), inferred (reasoned from several) or "
                  "speculative (hypothesis). Hypotheses MUST be labelled speculative. Each insight must cite "
                  "evidence_claim_ids from the matrix, give alternatives, a confidence (low/medium/high) with an "
                  "explanation, limitations and a next investigation. Return {\"insights\": [...]}.")
        drafts = ctx.llm.structured(prompt, InsightDraftList, ctx.budget, max_tokens=2500)
        if not drafts:
            return None
        insights = []
        for draft in drafts.insights:
            ids = [i for i in draft.evidence_claim_ids if i in claims]
            if not ids or len(ids) != len(draft.evidence_claim_ids):
                continue  # never accept an insight without real evidence
            try:
                kind = InsightKind(draft.kind.lower())
                label = EpistemicLabel(draft.label.lower())
            except ValueError:
                continue
            if kind == InsightKind.HYPOTHESIS:
                label = EpistemicLabel.SPECULATIVE
            confidence = draft.confidence.lower() if draft.confidence.lower() in ("low", "medium", "high") else "low"
            if label == EpistemicLabel.SPECULATIVE:
                confidence = "low" if confidence == "high" else confidence
            insights.append(Insight(
                id=_id("llm", draft.statement), kind=kind, label=label, statement=draft.statement[:500],
                evidence_claim_ids=ids, source_ids=list(dict.fromkeys(claims[i].source_id for i in ids)),
                reasoning=draft.reasoning[:600], alternatives=draft.alternatives[:4], confidence=confidence,
                confidence_explanation=draft.confidence_explanation[:300], limitations=draft.limitations[:4],
                next_investigation=draft.next_investigation[:300], generated_by="llm"))
        return insights if len(insights) >= 3 else None
