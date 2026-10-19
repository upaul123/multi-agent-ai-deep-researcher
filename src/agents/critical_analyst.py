"""Agent D: Critical Analysis. Extracts claims, links them to passages, builds the evidence matrix and
detects contradictions, methodological weaknesses and overstated conclusions."""
from __future__ import annotations

from src.services.observability import map_with_context
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from src.agents.base import Agent, RunContext
from src.analysis.claims import (ClaimDraftList, SubquestionMatcher, heuristic_claims, validate_llm_claims)
from src.analysis.matrix import build_matrix
from src.evidence.store import EvidenceStore
from src.models.evidence import Claim, Source
from src.orchestration.state import ResearchState
from src.services.llm import wrap_untrusted

MAX_LLM_SOURCES_PER_ITERATION = 12


class Verdict(BaseModel):
    index: int
    verdict: Literal["contradicts", "compatible", "unrelated"]
    explanation: str = ""


class Verdicts(BaseModel):
    verdicts: list[Verdict] = Field(default_factory=list)


class CriticalAnalystAgent(Agent):
    name = "critical_analyst"
    title = "Critical Analysis"
    responsibility = ("Extracts claims linked to exact passages, compares them across independent sources, and "
                      "flags contradictions, weak methods and overstated conclusions.")
    uses_llm = "optional"

    def run(self, state: ResearchState, ctx: RunContext) -> dict:
        plan = state["plan"]
        store = EvidenceStore(state.get("sources", []), state.get("passages", []))
        assessments = state.get("assessments", {})
        done = set(state.get("extracted_source_ids", []))
        matcher = SubquestionMatcher(plan)
        todo = [s for s in store.sources.values() if s.id not in done
                and "low_relevance" not in (assessments[s.id].flags if s.id in assessments else [])]
        claims: list[Claim] = list(state.get("claims", []))
        notes: list[str] = []

        llm_targets = todo[:MAX_LLM_SOURCES_PER_ITERATION] if ctx.llm_enabled else []
        llm_results: dict[str, list[Claim]] = {}
        if llm_targets:
            with ThreadPoolExecutor(max_workers=ctx.max_parallel) as pool:
                for source_id, extracted, rejected in map_with_context(pool,
                        lambda s: self._llm_extract(s, store, plan, matcher, ctx), llm_targets):
                    if extracted is not None:
                        llm_results[source_id] = extracted
                    if rejected:
                        notes.append(f"Rejected {rejected} LLM-proposed claim(s) that failed quotation or paraphrase integrity checks.")
        for source in todo:
            extracted = llm_results.get(source.id)
            if extracted is None:
                extracted = heuristic_claims(source, store.passages.get(source.id, []), plan, matcher)
            existing = {c.id for c in claims}
            for claim in extracted:  # the same quote can be proposed twice: keep one claim per id
                if claim.id not in existing:
                    existing.add(claim.id)
                    claims.append(claim)

        rows, links, contradictions = build_matrix(claims, store, assessments, heuristic=not ctx.llm_enabled)
        if ctx.llm_enabled and contradictions:
            contradictions, dismissed = self._adjudicate(contradictions, {c.id: c for c in claims}, ctx)
            if dismissed:
                notes.append(f"LLM review dismissed {dismissed} candidate contradiction(s) as compatible statements.")
        contested = sum(r.agreement.value in ("contested", "mixed") for r in rows)
        ctx.event(self.name, "completed",
                  f"{len(claims)} claims in {len(rows)} evidence rows; {len(contradictions)} contradiction(s); "
                  f"{contested} contested/mixed row(s)." + (" Claims extracted with LLM, quotes verified." if llm_results else ""),
                  state.get("iteration", 0), claims=len(claims), rows=len(rows), contradictions=len(contradictions))
        return {"claims": claims, "matrix": rows, "links": links, "contradictions": contradictions,
                "extracted_source_ids": list(done | {s.id for s in todo}), "warnings": sorted(set(notes))}

    def _adjudicate(self, contradictions, claims, ctx: RunContext):
        """Ask the LLM whether each heuristic candidate is a real contradiction (validated verdicts only)."""
        items = [c for c in contradictions if c.claim_a_id in claims and c.claim_b_id in claims][:15]
        listing = "\n".join(
            f"Pair {i}:\nA: " + wrap_untrusted(claims[c.claim_a_id].source_id, claims[c.claim_a_id].quote)
            + "\nB: " + wrap_untrusted(claims[c.claim_b_id].source_id, claims[c.claim_b_id].quote)
            for i, c in enumerate(items))
        prompt = ("For each numbered pair of quoted statements from different sources, decide whether they genuinely "
                  "contradict each other (incompatible claims about the same thing), are compatible, or are about "
                  "different things. Return {\"verdicts\": [{\"index\": 0, \"verdict\": \"contradicts|compatible|"
                  "unrelated\", \"explanation\": \"one sentence\"}]}.\n\n" + listing)
        result = ctx.llm.structured(prompt, Verdicts, ctx.budget, max_tokens=1200)
        if not result:
            return contradictions, 0
        verdicts = {v.index: v for v in result.verdicts if 0 <= v.index < len(items)}
        kept, dismissed = [], 0
        for i, con in enumerate(items):
            verdict = verdicts.get(i)
            if verdict and verdict.verdict in ("compatible", "unrelated"):
                dismissed += 1
                continue
            if verdict:
                con = con.model_copy(update={"explanation": verdict.explanation[:300] + " (LLM-reviewed)"})
            kept.append(con)
        return kept + contradictions[len(items):], dismissed

    def _llm_extract(self, source: Source, store: EvidenceStore, plan, matcher, ctx: RunContext):
        passages = store.passages.get(source.id, [])[:6]
        if not passages:
            return source.id, None, 0
        text = "\n\n".join(f"[{p.location}] {p.text}" for p in passages)
        prompt = ("Research request:\n" + wrap_untrusted("research_plan", plan.question + "\nSubquestions: "
                  + "; ".join(f"{s.id}: {s.text}" for s in plan.subquestions))
                  + "\n\nExtract up to 6 specific, checkable claims relevant to the research question from the source "
                    "below. For each, copy the supporting sentence EXACTLY into 'quote'. Classify claim_type and set "
                    "sceptical=true if the claim doubts or limits a premise. Return {\"claims\": [...]}.\n\n"
                  + wrap_untrusted(source.id, text[:6000]))
        drafts = ctx.llm.structured(prompt, ClaimDraftList, ctx.budget, max_tokens=1400)
        if drafts is None:
            return source.id, None, 0
        claims, rejected = validate_llm_claims(drafts, source, passages, matcher)
        return source.id, (claims or None), rejected
