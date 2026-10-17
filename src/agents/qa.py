"""Agent H: Research Q&A. Answers follow-up questions about a completed investigation.

It retrieves from the underlying evidence (claims, evidence matrix rows and source passages), not
only from the final report, cites sources with the report's reference numbers, surfaces conflicts and
gaps, and labels any use of general knowledge separately from the research corpus.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from src.analysis.text import truncate
from src.models.evidence import ORIGIN_LABELS
from src.models.research import ResearchBudget
from src.models.run import RunBundle
from src.orchestration.budget import RunBudget
from src.retrieval.bm25 import BM25Index
from src.services.llm import LLMClient, wrap_untrusted

logger = logging.getLogger(__name__)
AGREEMENT_TEXT = {"strong": "strong agreement", "moderate": "moderate agreement", "single_source": "single source",
                  "mixed": "mixed evidence", "contested": "contested"}


class QAAnswer(BaseModel):
    question: str
    answer: str
    basis: str  # research corpus | research corpus + general knowledge | insufficient evidence
    citations: list[dict] = Field(default_factory=list)  # {n, source_id, label, url, location, passage, origin}
    conflicts: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    how_reached: str = ""
    general_knowledge_note: str = ""
    method: str = "deterministic"


class _LLMAnswer(BaseModel):
    answer: str
    cited_ids: list[str] = Field(default_factory=list)
    uses_general_knowledge: bool = False
    general_knowledge_note: str = ""


class _Numbering:
    """Uses the report's reference numbers; sources not in the report get new numbers after them."""

    def __init__(self, bundle: RunBundle):
        self.bundle = bundle
        self.numbers = {r["source_id"]: r["n"] for r in (bundle.report.references if bundle.report else [])}
        self.next = max(self.numbers.values(), default=0) + 1
        self.used: dict[int, dict] = {}

    def cite(self, source_id: str, passage=None) -> str:
        if source_id not in self.numbers:
            self.numbers[source_id], self.next = self.next, self.next + 1
        n = self.numbers[source_id]
        source = self.bundle.source(source_id)
        if source and n not in self.used:
            self.used[n] = {"n": n, "source_id": source_id, "label": source.citation_label(), "url": source.url,
                            "origin": ORIGIN_LABELS[source.origin], "location": passage.location if passage else "",
                            "passage": truncate(passage.text, 400) if passage else ""}
        return f"[{n}]"


def answer_question(bundle: RunBundle, question: str, llm: LLMClient | None = None, use_llm: bool = False,
                    vectors=None) -> QAAnswer:
    question = question.strip()[:500]
    numbering = _Numbering(bundle)
    rows = BM25Index(bundle.matrix, lambda r: r.claim + " " + " ".join(r.weaknesses)).search(question, k=4)
    passages, semantic = _search_passages(bundle, question, vectors)
    claims_by_id = {c.id: c for c in bundle.claims}

    if not rows and not passages:
        return QAAnswer(question=question, basis="insufficient evidence",
                        answer="The research corpus for this investigation contains no evidence relevant to this question. "
                               "You can refine the investigation to research it.",
                        how_reached="No evidence row or passage matched the question's terms.")

    conflicts, gaps = [], []
    row_ids = {r.id for r, _ in rows}
    for con in bundle.contradictions:
        if con.row_id in row_ids or any(c in (con.claim_a_id, con.claim_b_id) for r, _ in rows
                                        for c in r.supporting_claim_ids + r.contradicting_claim_ids):
            a, b = claims_by_id.get(con.claim_a_id), claims_by_id.get(con.claim_b_id)
            if a and b:
                conflicts.append(f"\"{truncate(a.text, 140)}\" {numbering.cite(a.source_id)} vs "
                                 f"\"{truncate(b.text, 140)}\" {numbering.cite(b.source_id)}: {con.explanation}")
    sq_ids = {r.subquestion_id for r, _ in rows}
    gaps = [g.description for g in bundle.gaps if g.subquestion_id in sq_ids][:3]

    deterministic = _compose(bundle, rows, passages, numbering, claims_by_id)
    how = (f"Matched {len(rows)} evidence-matrix row(s) and {len(passages)} source passage(s) by "
           f"{'semantic similarity (Pinecone)' if semantic else 'keyword relevance'}. "
           "Agreement levels count independent sources only; copies of the same original count once.")
    answer = QAAnswer(question=question, answer=deterministic, basis="research corpus", conflicts=conflicts[:4],
                      gaps=gaps, how_reached=how)

    if use_llm and llm and llm.available:
        llm_answer = _llm_answer(bundle, question, rows, passages, llm)
        if llm_answer:
            text, cited, uses_general, note = llm_answer
            fresh = _Numbering(bundle)
            for sid, passage in cited:
                text = text.replace(f"[{sid}]", fresh.cite(sid, passage))
            answer.answer, answer.method = text, "llm (citations validated)"
            answer.basis = "research corpus + general knowledge" if uses_general else "research corpus"
            answer.general_knowledge_note = note
            fresh.used.update({k: v for k, v in numbering.used.items() if k not in fresh.used})
            numbering = fresh
    answer.citations = sorted(numbering.used.values(), key=lambda c: c["n"])
    if bundle.summary.mode == "demo":
        answer.answer += "\n\n_All sources in this investigation are synthetic demo data._"
    return answer


def _search_passages(bundle: RunBundle, question: str, vectors) -> tuple[list, bool]:
    """Best passages for a question, and whether they came from Pinecone (False: keyword search, or fallback)."""
    if vectors is not None and bundle.passages:
        try:
            vectors.ensure_indexed((p.id, p.text, {"kind": "passage", "source_id": p.source_id}) for p in bundle.passages)
            by_id = {p.id: p for p in bundle.passages}
            hits = [(by_id[pid], score) for pid, score in vectors.search(question, k=6, kind="passage") if pid in by_id]
            return hits, True
        except Exception:
            logger.warning("Semantic search failed for Q&A; using keyword search.", exc_info=True)
    return BM25Index(bundle.passages, lambda p: p.text).search(question, k=6), False


def _compose(bundle: RunBundle, rows, passages, numbering: _Numbering, claims_by_id) -> str:
    lines = ["**From the research corpus:**"]
    cited_sources = set()
    for row, _ in rows:
        claim = claims_by_id.get(row.supporting_claim_ids[0]) if row.supporting_claim_ids else None
        passage = next((p for p in bundle.passages if claim and p.id == claim.passage_id), None)
        marks = "".join(numbering.cite(s, passage if claim and s == claim.source_id else None)
                        for s in row.supporting_source_ids[:3])
        cited_sources |= set(row.supporting_source_ids[:3])
        line = f"- {truncate(row.claim, 260)} {marks} _({AGREEMENT_TEXT[row.agreement.value]}, evidence quality " \
               f"{row.evidence_quality.value})_"
        if row.contradicting_claim_ids:
            counter = claims_by_id.get(row.contradicting_claim_ids[0])
            if counter:
                line += f"\n  - However: \"{truncate(counter.text, 200)}\" {numbering.cite(counter.source_id)}"
        lines.append(line)
    extra = [(p, s) for p, s in passages if p.source_id not in cited_sources][:3]
    if extra:
        lines.append("\n**Other relevant passages:**")
        for passage, _ in extra:
            lines.append(f"- \"{truncate(passage.text, 240)}\" {numbering.cite(passage.source_id, passage)} "
                         f"({passage.location})")
    return "\n".join(lines)


def _llm_answer(bundle: RunBundle, question: str, rows, passages, llm: LLMClient):
    allowed = {}
    blocks = []
    for passage, _ in passages:
        allowed[passage.source_id] = passage
        blocks.append(wrap_untrusted(passage.source_id, f"[{passage.location}] {passage.text}"))
    row_text = "\n".join(f"- [{AGREEMENT_TEXT[r.agreement.value]}] {r.claim} (sources: {', '.join(r.supporting_source_ids[:3])})"
                         for r, _ in rows)
    for r, _ in rows:
        for sid in r.supporting_source_ids[:3]:
            allowed.setdefault(sid, next((p for p in bundle.passages if p.source_id == sid), None))
    prompt = ("Follow-up question about a completed investigation:\n" + wrap_untrusted("user_question", question)
              + "\n\nEvidence-matrix rows:\n" + wrap_untrusted("evidence_matrix", row_text) + "\n\n"
              "Source passages:\n" + "\n".join(blocks) +
              "\n\nAnswer using the evidence above. Cite sources inline by writing their id in square brackets, e.g. "
              "[src_abc123]. Point out conflicting evidence and missing evidence. If you add anything from general "
              "knowledge, set uses_general_knowledge=true and explain it in general_knowledge_note; never present it as "
              "coming from the sources. Return {\"answer\": ..., \"cited_ids\": [...], \"uses_general_knowledge\": ..., "
              "\"general_knowledge_note\": ...}.")
    result = llm.structured(prompt, _LLMAnswer, RunBudget(ResearchBudget(max_llm_calls=2, max_seconds=120)))
    if not result:
        return None
    cited = [(sid, allowed[sid]) for sid in result.cited_ids if sid in allowed]
    text = result.answer
    import re

    for marker in set(re.findall(r"\[(src_[0-9a-f]{12})\]", text)):
        if marker not in allowed:
            text = text.replace(f"[{marker}]", "")  # drop citations to sources that were not provided
        elif marker not in dict(cited):
            cited.append((marker, allowed[marker]))
    if not cited:
        return None
    return text, cited, result.uses_general_knowledge, result.general_knowledge_note[:400]
