"""Page 3: Research results. One investigation in tabs: overview, evidence, analysis, insights, full report and Q&A."""
import re
from html import escape

import streamlit as st

from src.models.run import ACTIVE_STATUSES
from src.reporting.render import to_html, to_markdown, to_pdf
from src.ui.components import DEPTH_LABELS, card, demo_banner, eyebrow, html, strip_citations
from src.ui.state import current_user, effective_settings, get_repo, preference, require_run, rerun_button
from src.ui.views import analysis, evidence, insights, qa, visualizations, report as report_view

bundle = require_run()
if bundle.summary.status in ACTIVE_STATUSES:  # results appear when the investigation finishes
    st.info("This investigation is still in progress. Follow it live; its results appear here when it finishes.",
            icon=":material/progress_activity:")
    st.page_link("app_pages/2_Research_Progress.py", label="Follow live", icon=":material/arrow_forward:")
    st.stop()
repo, settings, user = get_repo(), effective_settings(), current_user()
report = bundle.report
summary = bundle.summary
demo = summary.mode == "demo"
demo_banner(summary.mode)

# ------------------------------------------------------------------ heading + actions
done = summary.completed_at or summary.created_at
head, actions = st.columns([3, 1.3], vertical_alignment="bottom")
head.markdown('<div class="ds-report-head">' + eyebrow(f"Research report · {summary.status} {done:%d %b %Y}")
              + f'<div class="ds-title">{escape(summary.question)}</div>'
              + f'<div class="ds-meta"><span>✧ {DEPTH_LABELS.get(summary.depth, "Standard")} research</span>'
              + f"<span>{len(bundle.sources)} sources</span><span>{'Demo dataset' if demo else 'Live sources'}</span>"
              + f"<span>Updated {done:%d %b %Y %H:%M}</span></div></div>", unsafe_allow_html=True)
if summary.status == "failed":
    with st.container(border=True):
        note, again = st.columns([4, 1], vertical_alignment="center")
        note.error("This investigation did not finish" + (", so its report is incomplete" if report else "")
                   + ". Re-run it to start again with the same question and settings.", icon=":material/error:")
        with again:
            rerun_button(summary.id, key="results_rerun", type="primary", width="stretch")
        for error in bundle.errors:
            st.caption(error)
elif report is None:
    st.error("This run has no report. Its evidence can still be inspected below.")
    for error in bundle.errors:
        st.error(error)

a1, a2 = actions.columns(2)
if a1.button("Refine", icon=":material/replay:", width="stretch",
             help="Start a new investigation that builds on these findings"):
    findings = (report.section("key_findings").content if report and report.section("key_findings") else "")[:1500]
    st.session_state["refine_draft"] = {
        "question": summary.question, "parent_run_id": summary.id, "mode": "demo" if demo or user.is_guest else "live",
        "context": f"Refining a previous investigation. Earlier findings (verify, don't assume):\n{findings}"}
    st.switch_page("app_pages/1_Research_Workspace.py")
if report is not None:
    slug = re.sub(r"[^a-z0-9]+", "-", report.question.lower())[:50].strip("-") or "report"
    banner = demo and preference("include_demo_banner")
    with a2.popover("Export", icon=":material/download:", type="primary", width="stretch"):
        st.download_button("Markdown (.md)", data=to_markdown(report, banner), file_name=f"{slug}.md",
                           mime="text/markdown", width="stretch", on_click=repo.log_activity,
                           args=(user.id, "export", summary.id, {"format": "markdown"}))
        st.download_button("Web page (.html)", data=to_html(report, banner), file_name=f"{slug}.html",
                           mime="text/html", width="stretch", on_click=repo.log_activity,
                           args=(user.id, "export", summary.id, {"format": "html"}))
        pdf_key = f"pdf_{summary.id}"
        if st.button("Prepare PDF", width="stretch"):
            st.session_state[pdf_key] = to_pdf(report, banner)
        if st.session_state.get(pdf_key):
            st.download_button("PDF (.pdf)", data=st.session_state[pdf_key], file_name=f"{slug}.pdf",
                               mime="application/pdf", width="stretch", on_click=repo.log_activity,
                               args=(user.id, "export", summary.id, {"format": "pdf"}))
        elif pdf_key in st.session_state:
            st.caption("PDF rendering failed. Use HTML and print to PDF.")
st.write("")

# ------------------------------------------------------------------ overview helpers
rows = bundle.matrix
backed = sum(r.agreement.value in ("strong", "moderate") for r in rows)
share = backed / len(rows) if rows else 0
strength, strength_css = (("Strong", "ok") if share >= 0.5 else ("Moderate", "ok") if share >= 0.25
                          else ("Limited", "warn"))


def overview() -> None:
    main, side = st.columns([2.6, 1], gap="medium")
    with main, card("overview"):
        html('<div style="display:flex;align-items:center;gap:11px"><span class="ds-icon ds-purple">✧</span>'
             '<div><b style="font:700 15px Manrope,sans-serif">Executive summary</b><br><span class="ds-muted">'
             f"Drawn from {len(bundle.sources)} sources</span></div></div>")
        section = report.section("executive_summary") if report else None
        st.markdown(strip_citations(section.content) if section else "_No summary was produced._")
        top = sorted(bundle.insights, key=lambda i: (i.kind.value not in ("finding", "disagreement"),
                                                     {"high": 0, "medium": 1}.get(i.confidence, 2)))[:3]
        if top:
            html('<div class="ds-label" style="margin-top:14px">Key findings</div>' + "".join(
                f'<div class="ds-finding"><span>{n:02d}</span><p><b>{escape(i.statement)}</b> '
                f'<span class="ds-muted">{escape(i.label.value)} · {escape(i.confidence)} confidence</span></p></div>'
                for n, i in enumerate(top, start=1)))
    with side, card("glance"):
        html('<b style="font:700 14px Manrope,sans-serif">At a glance</b>'
             f'<div class="ds-stat"><span>Sources reviewed</span><b>{len(bundle.sources)}</b></div>'
             f'<div class="ds-stat"><span>Claims extracted</span><b>{len(bundle.claims)}</b></div>'
             f'<div class="ds-stat"><span>Contradictions</span><b>{len(bundle.contradictions)}</b></div>'
             f'<div class="ds-stat"><span>Evidence strength</span><b class="{strength_css}">{strength}</b></div>'
             '<hr style="margin:10px 0">'
             f'<b style="font:700 14px Manrope,sans-serif">Research team</b><div class="ds-muted" style="margin:6px 0">'
             f'<span style="color:#399174">✓</span> {sum(s.status == "completed" for s in bundle.statuses.values())} '
             "agents completed</div>")
        if st.button("View agent activity →", key="to_activity", width="stretch"):
            st.switch_page("app_pages/2_Research_Progress.py")
        topics = [s.perspective for s in (bundle.plan.subquestions if bundle.plan else [])][:6]
        if topics:
            html('<hr style="margin:10px 0"><b style="font:700 14px Manrope,sans-serif">Key topics</b>'
                 '<div class="ds-pills" style="margin-top:8px">'
                 + "".join(f"<span>{escape(t)}</span>" for t in dict.fromkeys(topics)) + "</div>")


def analysis_summary() -> None:
    gaps = [g for g in bundle.gaps if g.expected_value >= 0.35]
    cards = [("Evidence quality", "What the evidence supports",
              f"{backed} of {len(rows)} evidence rows reach moderate or strong agreement across independent sources; "
              f"{sum(r.independent_sources >= 2 for r in rows)} are backed by two or more.",
              f"{strength} · {len(bundle.sources)} sources", share),
             ("Contradictions & caveats", "Where sources differ",
              f"{len(bundle.contradictions)} contradiction(s) between independent sources; "
              f"{sum(r.agreement.value in ('contested', 'mixed') for r in rows)} contested or mixed rows.",
              "Details below", None),
             ("Knowledge gaps", "What remains uncertain",
              f"{len(gaps)} unresolved gap(s) at the end of the investigation.", "Open questions listed below", None)]
    for col, (label, title, text, foot, meter) in zip(st.columns(3), cards):
        bar = f'<div class="ds-meter"><i style="width:{int(meter * 100)}%"></i></div>' if meter is not None else ""
        col.markdown(f'<div class="ds-card" style="height:100%"><div class="ds-label">{label}</div>'
                     f'<div style="font:700 15px/1.4 Manrope,sans-serif;margin:8px 0">{title}</div>'
                     f'<p class="ds-muted" style="font-size:13px;line-height:1.7">{escape(text)}</p>{bar}'
                     f'<small class="ds-muted">{escape(foot)}</small></div>', unsafe_allow_html=True)
    st.write("")


labels = ["Overview", f"Evidence · {len(bundle.sources)}", "Analysis", f"Insights · {len(bundle.insights)}",
          f"Charts · {len(bundle.charts)}", "Full report", "Ask"]
t_overview, t_evidence, t_analysis, t_insights, t_charts, t_report, t_ask = st.tabs(labels)
with t_overview:
    overview()
with t_evidence:
    evidence.render(bundle)
with t_analysis:
    analysis_summary()
    analysis.render(bundle)
with t_insights:
    insights.render(bundle)
with t_charts:
    visualizations.render(bundle)
with t_report:
    if report is not None:
        report_view.render(bundle)
    else:
        st.caption("No report was produced for this run.")
with t_ask:
    qa.render(bundle, repo, settings, user)
