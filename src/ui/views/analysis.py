"""Results tab: Analysis. Evidence matrix, contradictions, agreement vs uncertainty, gaps and limitations."""
import pandas as pd
import streamlit as st

from src.models.run import RunBundle
from src.ui import charts
from src.ui.components import card


def render(bundle: RunBundle) -> None:
    ref = {r["source_id"]: r["n"] for r in (bundle.report.references if bundle.report else [])}
    claims = {c.id: c for c in bundle.claims}
    sq_text = {s.id: s.text for s in (bundle.plan.subquestions if bundle.plan else [])}


    def cite(ids: list[str]) -> str:
        return " ".join(f"[{ref[i]}]" if i in ref else (bundle.source(i).title[:30] + "…" if bundle.source(i) else i)
                        for i in dict.fromkeys(ids))


    m = st.columns(5)
    m[0].metric("Claims", len(bundle.claims))
    m[1].metric("Evidence rows", len(bundle.matrix))
    m[2].metric("Contradictions", len(bundle.contradictions))
    m[3].metric("Contested / mixed", sum(r.agreement.value in ("contested", "mixed") for r in bundle.matrix))
    m[4].metric("Corroborated (≥2 independent)", sum(r.independent_sources >= 2 for r in bundle.matrix))

    left, right = st.columns(2)
    with left:
        st.subheader("Agreement across evidence rows")
        st.plotly_chart(charts.agreement_bar(bundle.matrix), width="stretch")
    with right:
        st.subheader("Source-quality flags")
        if any(a.flags for a in bundle.assessments.values()):
            st.plotly_chart(charts.flags_bar(bundle.assessments), width="stretch")
        else:
            st.caption("No sources were flagged.")
    st.caption("Agreement counts independent sources only. A news story that repeats a press release counts once.")

    st.subheader("Contradictions and competing interpretations")
    if not bundle.contradictions:
        st.caption("No contradictions were detected between independent sources.")
    for con in bundle.contradictions:
        a, b = claims.get(con.claim_a_id), claims.get(con.claim_b_id)
        if not (a and b):
            continue
        with card(f"con-{con.claim_a_id}-{con.claim_b_id}"):
            st.markdown(f"**{con.nature.title()} contradiction** · {con.topic[:120]}")
            c1, c2 = st.columns(2)
            sa, sb = bundle.source(a.source_id), bundle.source(b.source_id)
            c1.markdown(f"> {a.quote}\n\n**{cite([a.source_id])}** {sa.title if sa else ''} · {a.claim_type.value}")
            c2.markdown(f"> {b.quote}\n\n**{cite([b.source_id])}** {sb.title if sb else ''} · {b.claim_type.value}")
            st.caption(con.explanation)

    st.subheader("Evidence matrix")
    f1, f2 = st.columns(2)
    agreements = sorted({r.agreement.value for r in bundle.matrix})
    chosen = f1.multiselect("Agreement", agreements, default=agreements)
    subqs = sorted({r.subquestion_id for r in bundle.matrix if r.subquestion_id})
    chosen_sq = f2.multiselect("Subquestion", subqs, default=subqs, format_func=lambda s: f"{s}: {sq_text.get(s, '')[:60]}")
    rows = [r for r in bundle.matrix if r.agreement.value in chosen and (r.subquestion_id in chosen_sq or not r.subquestion_id)]
    st.dataframe(pd.DataFrame([{
        "Claim": r.claim, "Type": r.claim_type.value, "Supporting": cite(r.supporting_source_ids),
        "Contradicting": cite(r.contradicting_source_ids) or "–", "Independent sources": r.independent_sources,
        "Agreement": r.agreement.value.replace("_", " "), "Quality": r.evidence_quality.value,
        "Weaknesses": "; ".join(r.weaknesses), "Unresolved": "; ".join(r.unresolved_questions),
        "Subquestion": r.subquestion_id} for r in rows]), hide_index=True, width="stretch", height=420)

    strong = [r for r in bundle.matrix if r.agreement.value in ("strong", "moderate")]
    uncertain = [r for r in bundle.matrix if r.agreement.value in ("contested", "mixed")]
    a_col, u_col = st.columns(2)
    with a_col:
        st.subheader("Areas of agreement")
        for r in strong[:8]:
            st.markdown(f"- {r.claim[:220]} {cite(r.supporting_source_ids)}  \n  "
                        f"<span class='dr-muted'>{r.assessment}</span>", unsafe_allow_html=True)
        if not strong:
            st.caption("No claim is corroborated by two or more independent sources.")
    with u_col:
        st.subheader("Areas of uncertainty")
        for r in uncertain[:8]:
            st.markdown(f"- {r.claim[:220]}  \n  <span class='dr-muted'>{r.assessment}</span>", unsafe_allow_html=True)
        single = sum(r.agreement.value == "single_source" for r in bundle.matrix)
        st.caption(f"{single} evidence row(s) rest on a single independent source and are not corroborated.")

    st.subheader("Research gaps")
    if bundle.gaps:
        st.dataframe(pd.DataFrame([{"Gap": g.kind.value.replace("_", " "), "Description": g.description,
                                    "Priority": g.priority, "Expected value": g.expected_value,
                                    "Suggested follow-up": "; ".join(g.follow_up_queries)} for g in bundle.gaps]),
                     hide_index=True, width="stretch")
    else:
        st.caption("No gaps remained at the end of the investigation.")
    if bundle.plan:
        st.subheader("Subquestion coverage")
        st.dataframe(pd.DataFrame([{"Id": s.id, "Subquestion": s.text, "Perspective": s.perspective, "Priority": s.priority,
                                    "Status": s.status.value, "Added in round": s.added_in_iteration}
                                   for s in bundle.plan.subquestions]), hide_index=True, width="stretch")
