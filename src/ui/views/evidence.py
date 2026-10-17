"""Results tab: Evidence. Sources, metadata, assessments, passages and the claims they support."""
import streamlit as st

from src.models.evidence import ORIGIN_LABELS
from src.models.run import RunBundle
from src.ui import charts
from src.ui.components import flags_html, link_for, origin_badge, source_header


def render(bundle: RunBundle) -> None:
    ref_numbers = {r["source_id"]: r["n"] for r in (bundle.report.references if bundle.report else [])}
    claims_by_source: dict[str, list] = {}
    for claim in bundle.claims:
        claims_by_source.setdefault(claim.source_id, []).append(claim)
    row_of_claim = {cid: (row, "supports") for row in bundle.matrix for cid in row.supporting_claim_ids}
    row_of_claim |= {cid: (row, "contradicts") for row in bundle.matrix for cid in row.contradicting_claim_ids}

    c = st.columns(4)
    c[0].metric("Sources", len(bundle.sources))
    c[1].metric("Passages", len(bundle.passages))
    c[2].metric("Primary sources", sum(a.is_primary for a in bundle.assessments.values()))
    c[3].metric("Cited in report", len(ref_numbers))
    chart_l, chart_r = st.columns(2)
    chart_l.plotly_chart(charts.source_types_pie(bundle.sources), width="stretch")
    chart_r.plotly_chart(charts.quality_relevance_scatter(bundle.sources, bundle.assessments), width="stretch")
    st.caption("Relevance and quality are heuristic triage indicators, not calibrated measures of truth.")

    f = st.columns([2, 1.5, 1.5, 1.2])
    text = f[0].text_input("Search sources", placeholder="title, publisher or text")
    types = sorted({s.source_type.value for s in bundle.sources})
    chosen_types = f[1].multiselect("Source type", types, default=types)
    origins = sorted({s.origin.value for s in bundle.sources})
    chosen_origins = f[2].multiselect("Origin", origins, default=origins,
                                      format_func=lambda o: next(v for k, v in ORIGIN_LABELS.items() if k.value == o))
    qualities = ["high", "medium", "low", "unknown"]
    chosen_quality = f[3].multiselect("Quality", qualities, default=qualities)
    years = sorted({s.published_date.year for s in bundle.sources if s.published_date})
    year_range = st.slider("Publication year", years[0], years[-1], (years[0], years[-1])) if len(years) > 1 else None
    include_undated = st.checkbox("Include sources without a date", value=True)
    min_relevance = st.slider("Minimum relevance", 0.0, 1.0, 0.0, 0.05)

    sort = st.segmented_control("Sort by", ["relevance", "date", "quality", "report order"], default="relevance") or "relevance"
    order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


    def keep(source) -> bool:
        a = bundle.assessments.get(source.id)
        if source.source_type.value not in chosen_types or source.origin.value not in chosen_origins:
            return False
        if a and (a.evidence_quality.value not in chosen_quality or a.relevance < min_relevance):
            return False
        if text and text.lower() not in f"{source.title} {source.publisher} {source.content}".lower():
            return False
        if source.published_date and year_range and not year_range[0] <= source.published_date.year <= year_range[1]:
            return False
        return include_undated or bool(source.published_date)


    sources = [s for s in bundle.sources if keep(s)]
    keyfn = {"relevance": lambda s: -(bundle.assessments[s.id].relevance if s.id in bundle.assessments else 0),
             "date": lambda s: -(s.published_date.toordinal() if s.published_date else 0),
             "quality": lambda s: order[bundle.assessments[s.id].evidence_quality.value] if s.id in bundle.assessments else 9,
             "report order": lambda s: ref_numbers.get(s.id, 999)}[sort]
    st.caption(f"Showing {len(sources)} of {len(bundle.sources)} sources")
    for source in sorted(sources, key=keyfn):
        a = bundle.assessments.get(source.id)
        with st.expander(source_header(source, a, ref_numbers.get(source.id))):
            st.markdown(origin_badge(source.origin) + " " + flags_html(a), unsafe_allow_html=True)
            meta = st.columns(4)
            meta[0].markdown(f"**Type:** {source.source_type.value.replace('_', ' ')}" +
                             (f" ({'primary' if a.is_primary else 'secondary'})" if a else ""))
            meta[1].markdown(f"**Relevance:** {a.relevance:.2f}" if a else "**Relevance:** —")
            meta[2].markdown(f"**Freshness:** {a.freshness.value}" if a else "")
            meta[3].markdown(f"**Retrieved:** {source.retrieved_at:%d %b %Y} via {source.retrieval_method}")
            if source.authors:
                st.caption("Authors: " + ", ".join(source.authors))
            if source.doc_ref:
                st.caption(f"Identifier: {source.doc_ref}")
            link = link_for(source)
            if link:
                st.link_button(link[0], link[1])
            if a:
                st.markdown(f"_{a.rationale}_")
                if a.authority_indicators:
                    st.caption("Authority indicators: " + "; ".join(a.authority_indicators))
                for limitation in a.limitations:
                    st.caption(f"Limitation: {limitation}")
            if source.related_ids:
                related = [bundle.source(r) for r in source.related_ids]
                st.info("Related / duplicated content: " + "; ".join(r.title for r in related if r), icon=":material/link:")
            if source.references_found:
                st.caption("References found inside: " + ", ".join(source.references_found[:6]))
            if not source.full_text_available:
                st.caption("Only a snippet or abstract was retrieved; the full text was not reviewed.")
            st.markdown("**Extracted passages**")
            for passage in bundle.passages_for(source.id)[:6]:
                st.markdown(f"> {passage.text[:900]}\n\n<span class='dr-muted'>{passage.location}</span>", unsafe_allow_html=True)
            claims = claims_by_source.get(source.id, [])
            if claims:
                st.markdown("**Claims from this source**")
                for claim in claims:
                    row, relation = row_of_claim.get(claim.id, (None, ""))
                    tag = (f"{relation} · {row.agreement.value.replace('_', ' ')}" if row else "")
                    st.markdown(f"- {claim.text}  \n  <span class='dr-muted'>{claim.claim_type.value} · {tag} · "
                                f"{claim.location}</span>", unsafe_allow_html=True)
