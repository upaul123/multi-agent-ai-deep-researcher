"""Agent G: Report Builder. Assembles a structured, cited report and validates every citation.

Sections are built deterministically from the plan, evidence matrix, contradictions, gaps and insights,
so every statement traces back to claims and sources. In live mode the LLM may rewrite the executive
summary and detailed analysis as prose. Its citation markers must refer to existing references, and the
rewrite is rejected if it introduces numbers that do not appear in the evidence.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from src.agents.base import Agent, RunContext
from src.analysis.text import truncate
from src.evidence.citations import build_references, format_reference, strip_invalid_markers, validate_citations
from src.evidence.store import EvidenceStore
from src.models.evidence import (Agreement, EpistemicLabel, InsightKind, ORIGIN_LABELS, Report, ReportSection)
from src.models.research import ResearchMode
from src.orchestration.state import ResearchState
from src.services.llm import wrap_untrusted

AGREEMENT_LABEL = {Agreement.STRONG: "strong agreement", Agreement.MODERATE: "moderate agreement",
                   Agreement.SINGLE_SOURCE: "single source", Agreement.MIXED: "mixed evidence",
                   Agreement.CONTESTED: "contested"}


class Citer:
    """Assigns reference numbers to sources in order of first citation."""

    def __init__(self, store: EvidenceStore):
        self.store, self.order = store, []

    def __call__(self, source_ids: list[str], limit: int = 4) -> str:
        markers = []
        for sid in list(dict.fromkeys(source_ids))[:limit]:
            if sid not in self.store.sources:
                continue
            if sid not in self.order:
                self.order.append(sid)
            markers.append(f"[{self.order.index(sid) + 1}]")
        return "".join(markers)


class ProseDraft(BaseModel):
    executive_summary: str
    detailed_analysis: str


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", re.sub(r"\[\d+\]", "", text))}


def _cell(text: str) -> str:
    """Text that is safe inside a markdown table cell (or on one line)."""
    return " ".join(str(text).replace("|", "/").split())


def _strength(backed: int, total: int) -> str:
    share = backed / total if total else 0
    return "strong" if share >= 0.5 else "moderate" if share >= 0.25 else "limited"


class ReportBuilderAgent(Agent):
    name = "report_builder"
    title = "Report Builder"
    responsibility = "Synthesises a structured report with inline citations, limitations and a validated reference list."
    uses_llm = "optional"

    def run(self, state: ResearchState, ctx: RunContext) -> dict:
        report = self.build(state, ctx)
        invalid = [c for c in report.citation_checks if not c.valid]
        ctx.event(self.name, "completed", f"Report with {len(report.sections)} sections and {len(report.references)} "
                  f"references; {len(report.citation_checks) - len(invalid)}/{len(report.citation_checks)} citations "
                  "passed traceability checks.", state.get("iteration", 0), references=len(report.references))
        return {"report": report}

    def build(self, state: ResearchState, ctx: RunContext) -> Report:
        request, plan = state["request"], state["plan"]
        store = EvidenceStore(state.get("sources", []), state.get("passages", []))
        claims = {c.id: c for c in state.get("claims", [])}
        rows = state.get("matrix", [])
        insights = state.get("insights", [])
        contradictions = state.get("contradictions", [])
        gaps = state.get("gaps", [])
        assessments = state.get("assessments", {})
        usage = state.get("usage")
        cite = Citer(store)
        demo = request.mode == ResearchMode.DEMO
        sections: list[ReportSection] = []

        def add(key: str, title: str, content: str) -> None:
            sections.append(ReportSection(key=key, title=title, content=content.strip() or "_No content._"))

        # Introduction: objective, why it matters, scope and approach
        origins = {}
        for s in store.sources.values():
            origins[ORIGIN_LABELS[s.origin]] = origins.get(ORIGIN_LABELS[s.origin], 0) + 1
        mode_note = ("> **Demo mode:** all sources are SYNTHETIC and fictional; findings illustrate the workflow and are "
                     "not real evidence." if demo else
                     "> **Live research:** sources were retrieved from the configured providers during this run "
                     "(cached results are labelled).")
        why = (f"It was requested with this background: {_cell(truncate(request.context, 300))}" if request.context else
               "It gives decision makers a cited, balanced view of what the evidence shows, where sources disagree and "
               "what remains uncertain.")
        glance = [("Timeframe", plan.scope.timeframe)]
        if plan.scope.geography:
            glance.append(("Geography", plan.scope.geography))
        glance += [("Research rounds", str(state.get("iteration", 0) + 1)),
                   ("Searches run", str(usage.searches if usage else 0)),
                   ("Sources reviewed", f"{len(store.sources)}" + (
                       f" ({', '.join(f'{v} {k.lower()}' for k, v in origins.items())})" if origins else "")),
                   ("Claims extracted", str(len(claims))),
                   ("Stopping rule", state.get("stop_reason") or state.get("route_reason") or "n/a")]
        introduction = "\n".join([
            mode_note, "",
            f"**Objective.** This report answers the question: _{_cell(request.question)}_ {why}", "",
            "**Goals of the investigation**", "", *[f"- {o}" for o in plan.objectives], "",
            "**Scope and approach**", "", "| Item | Detail |", "|---|---|",
            *[f"| {k} | {_cell(v)} |" for k, v in glance], "",
            f"**Search strategy.** {plan.search_strategy}", "",
            "**Subquestions investigated**", "",
            "| # | Subquestion | Perspective | Priority | Status |", "|---|---|---|---|---|",
            *[f"| {n} | {_cell(sq.text)} | {_cell(sq.perspective)} | {sq.priority} | {sq.status.value} |"
              for n, sq in enumerate(plan.subquestions, start=1)], "",
            "_Source assessments are rule-based indicators (type, date, wording, duplication), not calibrated "
            "probabilities._",
        ])

        # Executive summary (deterministic draft)
        findings = [i for i in insights if i.kind == InsightKind.FINDING]
        disagreements = [i for i in insights if i.kind == InsightKind.DISAGREEMENT]
        hypotheses = [i for i in insights if i.label == EpistemicLabel.SPECULATIVE]
        answered = sum(sq.status.value == "answered" for sq in plan.subquestions)
        backed = sum(r.agreement in (Agreement.STRONG, Agreement.MODERATE) for r in rows)
        strength = _strength(backed, len(rows))
        summary = [f"**Objective:** {_cell(request.question)}", "",
                   f"**Bottom line:** independent corroboration is **{strength}** ({backed} of {len(rows)} evidence "
                   f"clusters are backed by two or more independent sources). {answered} of {len(plan.subquestions)} "
                   f"subquestions met the evidence threshold, drawing on {len(store.sources)} sources.", ""]
        for f in findings[:3]:
            summary.append(f"- **Finding ({f.confidence} confidence):** {truncate(f.statement, 260)} {cite(f.source_ids)}")
        for d in disagreements[:2]:
            summary.append(f"- **Disagreement:** {truncate(d.statement, 260)} {cite(d.source_ids)}")
        if hypotheses:
            summary.append(f"- **Hypothesis (speculative):** {truncate(hypotheses[0].statement.removeprefix('Hypothesis: '), 220)} "
                           f"{cite(hypotheses[0].source_ids)}")
        if not findings and not disagreements:
            summary.append("- The evidence gathered was too thin to support firm findings; see the recommendations "
                           "and limitations below.")
        exec_summary = "\n".join(summary)

        # Analysis by subquestion
        detail = []
        for sq in plan.subquestions:
            sq_rows = sorted([r for r in rows if r.subquestion_id == sq.id], key=lambda r: -r.independent_sources)
            detail.append(f"### {sq.text}\n_Status: {sq.status.value} · {len(sq_rows)} evidence cluster(s)_\n")
            if not sq_rows:
                detail.append("No relevant evidence was found for this subquestion.\n")
                continue
            for row in sq_rows[:4]:
                line = f"- {truncate(row.claim, 280)} {cite(row.supporting_source_ids)} ({AGREEMENT_LABEL[row.agreement]}"
                line += f", evidence quality {row.evidence_quality.value})"
                if row.contradicting_claim_ids:
                    counter = claims.get(row.contradicting_claim_ids[0])
                    if counter:
                        line += f". Contradicted by: \"{truncate(counter.text, 200)}\" {cite(row.contradicting_source_ids)}"
                if row.weaknesses:
                    line += f". Caveats: {'; '.join(row.weaknesses[:2])}"
                detail.append(line)
            detail.append("")
        detailed = "\n".join(detail)

        # Evidence overview (matrix)
        table = ["| Claim | Supporting | Contradicting | Agreement | Quality |", "|---|---|---|---|---|"]
        for row in sorted(rows, key=lambda r: (r.agreement != Agreement.CONTESTED, -r.independent_sources))[:15]:
            table.append(f"| {_cell(truncate(row.claim, 110))} | {cite(row.supporting_source_ids) or '–'} | "
                         f"{cite(row.contradicting_source_ids) or '–'} | {AGREEMENT_LABEL[row.agreement]} | "
                         f"{row.evidence_quality.value} |")
        if not rows:
            table = ["No claims were extracted, so there is no evidence to compare."]
        if contradictions:
            table.append("\n**Contradictions identified:**\n")
            for con in contradictions[:8]:
                a, b = claims.get(con.claim_a_id), claims.get(con.claim_b_id)
                if a and b:
                    table.append(f"- ({con.nature}) \"{truncate(a.text, 140)}\" {cite([a.source_id])} vs "
                                 f"\"{truncate(b.text, 140)}\" {cite([b.source_id])}: {con.explanation}")
        evidence_section = "\n".join(table)

        # Findings: key findings table, then trends, implications and risks
        def insight_lines(kinds: set) -> str:
            lines = []
            for i in insights:
                if i.kind in kinds:
                    tag = {"observed": "Observed", "inferred": "Inferred", "speculative": "Speculative"}[i.label.value]
                    lines.append(f"- **{tag}:** {i.statement} {cite(i.source_ids)}  \n  _Confidence {i.confidence}: "
                                 f"{i.confidence_explanation}_" + (f"  \n  _Alternatives: {'; '.join(i.alternatives[:2])}_"
                                                                   if i.alternatives else ""))
            return "\n".join(lines)
        if findings:
            key = ["| # | Finding | Confidence | Basis |", "|---|---|---|---|",
                   *[f"| {n} | {_cell(f.statement)} {cite(f.source_ids)} | {f.confidence} | "
                     f"{_cell(f.confidence_explanation)} |" for n, f in enumerate(findings, start=1)]]
        else:
            key = ["No claim reached moderate or strong agreement across independent sources."]
        key += ["", "### Emerging trends and hypotheses",
                insight_lines({InsightKind.TREND, InsightKind.HYPOTHESIS}) or "None identified.",
                "", "### Implications and opportunities",
                insight_lines({InsightKind.IMPLICATION, InsightKind.OPPORTUNITY}) or "None identified.",
                "", "### Risks and alternative interpretations",
                insight_lines({InsightKind.RISK, InsightKind.DISAGREEMENT}) or "None identified."]
        key_findings = "\n".join(key)

        # Recommendations: actionable next steps, each tied to the evidence behind it
        recs = []
        for f in [f for f in findings if f.confidence in ("high", "medium")][:2]:
            recs.append(f"**Build on well-supported evidence.** {truncate(f.statement, 220)} {cite(f.source_ids)}")
        for i in [i for i in insights if i.kind in (InsightKind.OPPORTUNITY, InsightKind.IMPLICATION)][:2]:
            recs.append(f"**Assess this opportunity.** {truncate(i.statement, 220)} {cite(i.source_ids)}")
        for i in [i for i in insights if i.kind == InsightKind.RISK][:2]:
            recs.append(f"**Plan for this risk.** {truncate(i.statement, 220)} {cite(i.source_ids)}")
        for row in [r for r in rows if r.agreement in (Agreement.CONTESTED, Agreement.MIXED)][:1]:
            recs.append(f"**Resolve the disagreement before relying on it.** {truncate(row.claim, 200)} "
                        f"{cite(row.supporting_source_ids + row.contradicting_source_ids)}")
        for g in sorted(gaps, key=lambda g: -g.expected_value)[:2]:
            if g.follow_up_queries:
                recs.append(f"**Close an evidence gap.** {g.description} Suggested searches: "
                            + ", ".join(f"“{q}”" for q in g.follow_up_queries[:2]))
        if rows and strength == "limited":
            recs.append("**Seek independent corroboration.** Most claims rest on a single source; look for independent "
                        "studies or primary data before acting on them.")
        recs += [f"**Investigate further.** {i.next_investigation}" for i in insights if i.next_investigation][:2]
        recs = list(dict.fromkeys(recs))[:8]
        next_steps = ("\n".join(f"{n}. {r}" for n, r in enumerate(recs, start=1))
                      or "No specific actions are recommended at this depth; the evidence was too limited.")

        # Conclusion
        if rows:
            conclusion = [f"Independent corroboration of the evidence gathered is **{strength}**: {answered} of "
                          f"{len(plan.subquestions)} subquestions met the evidence threshold, and {backed} of {len(rows)} "
                          "evidence clusters are corroborated by two or more independent sources."]
            if findings:
                conclusion.append(f"The best-supported finding is: {truncate(findings[0].statement, 240)} "
                                  f"{cite(findings[0].source_ids)}")
            if disagreements:
                conclusion.append(f"The main open disagreement is: {truncate(disagreements[0].statement, 400)} "
                                  f"{cite(disagreements[0].source_ids)}")
            conclusion.append("Treat these conclusions as a cited starting point and verify important points against "
                              "the original sources before acting on them.")
        else:
            conclusion = ["The research did not find enough relevant evidence to answer the question, so no "
                          "conclusion is drawn. The question may be too narrow or use unusual terms, or the configured "
                          "sources may not cover it. Rephrase the question or broaden its scope."]
        if demo:
            conclusion.append("Because every source is synthetic demo data, these conclusions illustrate the workflow "
                              "only.")
        conclusion_text = "\n\n".join(conclusion)

        # Limitations
        abstract_only = sum(not s.full_text_available for s in store.sources.values())
        flagged = {f for a in assessments.values() for f in a.flags}
        limits = [
            "- Citations were checked for traceability (the source exists, the reference matches, the passage is "
            "relevant). This does **not** verify that the claims are true.",
            "- Source scores are heuristic triage indicators, not calibrated probabilities.",
        ]
        if demo:
            limits.insert(0, "- **All sources in this report are synthetic demo data.** No live research was performed.")
        if abstract_only:
            limits.append(f"- {abstract_only} source(s) were reviewed from a snippet or abstract only, not the full text.")
        if "repeats_other_source" in flagged:
            limits.append("- Some sources repeat other sources (e.g. news reprinting a press release); they were not "
                          "counted as independent corroboration.")
        if "prompt_injection_attempt" in flagged:
            limits.append("- At least one source contained instructions aimed at AI systems. They were ignored and the "
                          "source was down-weighted.")
        if state.get("usage") and state["usage"].exhausted:
            limits.append(f"- Research stopped partly because limits were reached: {', '.join(state['usage'].exhausted)}.")
        unresolved = sorted({q for r in rows for q in r.unresolved_questions})
        if unresolved:
            limits.append("\n**Unresolved questions:**\n\n" +"\n".join(f"- {q}" for q in unresolved[:6]))
        if state.get("errors"):
            limits.append("\n**Errors during the run:**\n\n" +"\n".join(f"- {e}" for e in state["errors"][:5]))
        limitations = "\n".join(limits)

        # Optional LLM prose for summary + analysis, validated.
        generated_by = "heuristic"
        if ctx.llm_enabled and rows:
            prose = self._llm_prose(request.question, exec_summary, detailed, store, cite, ctx)
            if prose:
                exec_summary, detailed = prose
                generated_by = "llm (citations validated)"

        # Introduction → Analysis → Findings → Recommendations → Conclusion, with the summary first and the
        # supporting material (limitations, chart data, sources) after.
        add("executive_summary", "Executive summary", exec_summary)
        add("introduction", "Introduction", introduction)
        add("detailed_analysis", "Analysis", detailed)
        add("evidence", "Evidence overview", evidence_section)
        add("key_findings", "Findings", key_findings)
        add("next_steps", "Recommendations", next_steps)
        add("conclusion", "Conclusion", conclusion_text)
        add("limitations", "Limitations and unresolved questions", limitations)

        # One table per kind of chart; the same figure reported by several charts is listed once.
        chart_lines, grouped = [], {}
        for chart in state.get("charts", []):
            grouped.setdefault((chart.title, chart.unit, chart.limitation, chart.synthetic), []).extend(chart.points)
        for (title, unit, limitation, synthetic), points in grouped.items():
            chart_lines += [f"### {title}" + (" (synthetic)" if synthetic else ""), f"_Unit: {unit}. {limitation}_", "",
                            "| Figure | Value | Source location |", "|---|---|---|"]
            seen = set()
            for point in points:
                if (point.label, point.value) not in seen:
                    seen.add((point.label, point.value))
                    chart_lines.append(f"| {_cell(point.label)} | **{point.value:g} {_cell(unit)}** "
                                       f"{cite([point.source_id])} | {_cell(point.location)} |")
            chart_lines.append("")
        add("visualizations", "Data behind the charts", "\n".join(chart_lines)
            or "No unambiguous numeric dataset was available; no chart values were invented.")
        references = build_references([store.sources[sid] for sid in cite.order])
        add("references", "References", "\n".join(format_reference(r) for r in references) or "_No sources cited._")

        body = "\n\n".join(s.content for s in sections if s.key != "references")
        checks = validate_citations(body, references, store.sources, store.passages)
        return Report(title=f"Research report: {truncate(request.question, 120)}", question=request.question,
                      sections=sections, references=references, citation_checks=checks, generated_by=generated_by)

    def _llm_prose(self, question: str, summary: str, detailed: str, store: EvidenceStore, cite: Citer,
                   ctx: RunContext) -> tuple[str, str] | None:
        valid = set(range(1, len(cite.order) + 1))
        prompt = ("Research question:\n" + wrap_untrusted("research_question", question)
                  + "\nRewrite the two draft sections below into clear, balanced prose for a "
                  "decision maker. Use short, precise sentences, a neutral and professional tone, and plain language "
                  "(explain any necessary jargon). Keep the opening objective and bottom-line lines of the executive "
                  "summary first, then use bullet points for the key points. Keep every citation marker like [3] next "
                  "to the statement it supports; do not add new markers, facts or numbers. Keep disagreements and uncertainty explicit, and keep speculative "
                  "statements labelled as hypotheses. Return {\"executive_summary\": ..., \"detailed_analysis\": ...} "
                  "(markdown allowed; keep the ### subquestion headings in the analysis).\n\n"
                  "DRAFT EXECUTIVE SUMMARY:\n" + wrap_untrusted("draft_summary", summary)
                  + "\n\nDRAFT DETAILED ANALYSIS:\n" + wrap_untrusted("draft_analysis", detailed[:9000]))
        draft = ctx.llm.structured(prompt, ProseDraft, ctx.budget, max_tokens=3000)
        if not draft:
            return None
        for original, rewritten in ((summary, draft.executive_summary), (detailed[:9000], draft.detailed_analysis)):
            if set(re.findall(r"\[\d+\]", original)) != set(re.findall(r"\[\d+\]", rewritten)):
                ctx.event(self.name, "info", "LLM rewrite rejected: citation markers were changed or removed.")
                return None
        allowed_numbers = _numbers(summary + detailed + " ".join(s.content for s in store.sources.values()))
        new_numbers = {n for n in _numbers(draft.executive_summary + draft.detailed_analysis)
                       if n not in allowed_numbers and not (n.isdigit() and int(n) < 13)}
        if new_numbers:
            ctx.event(self.name, "info", "LLM rewrite rejected: it introduced numbers not present in the evidence.")
            return None
        es, removed_a = strip_invalid_markers(draft.executive_summary, valid)
        da, removed_b = strip_invalid_markers(draft.detailed_analysis, valid)
        if removed_a + removed_b:
            ctx.event(self.name, "info", f"Removed {removed_a + removed_b} citation marker(s) that matched no reference.")
        return es, da
