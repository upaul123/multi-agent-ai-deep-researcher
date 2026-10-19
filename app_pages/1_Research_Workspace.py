"""Page 1: Research workspace. Define an investigation and start it.

Demo mode (everyone, and the only mode for guests) runs one of the preselected DEMO_QUESTIONS against the synthetic
corpus. Live mode (signed-in users) accepts any question.
"""
from datetime import date

import streamlit as st
from pydantic import ValidationError

from src.ingestion.extract import ALLOWED_UPLOADS, ExtractionError, extract_upload
from src.models.research import (DEMO_QUESTIONS, DEPTH_PRESETS, Depth, ResearchMode, ResearchRequest,
                                 SourcePreference)
from src.models.research import QUESTION_EVIDENCE_NOTICE, question_guidance, question_problem
from src.ui.components import DEPTH_LABELS, ago, card, eyebrow, html, mode_tag, status_tag
from src.models.run import ACTIVE_STATUSES
from src.services.jobs import RunRejected
from src.ui.state import (current_user, effective_settings, get_repo, get_run_manager, open_progress, open_run,
                          preference, provider_settings)

repo, settings, user = get_repo(), effective_settings(), current_user()
DEMO_CAPTIONS = ["Technology outlook · the richest sample run",
                 "Markets & opportunities · a different angle",
                 "Weighing conflicting evidence · shows contradictions and follow-up research"]
DEPTH_ICONS = {"quick": "⚡", "standard": "◉", "deep": "✧"}
PREF_LABELS = {SourcePreference.WEB: "Web", SourcePreference.ACADEMIC: "Academic papers", SourcePreference.NEWS: "News",
               SourcePreference.OFFICIAL: "Official / regulatory", SourcePreference.UPLOADED: "My documents",
               SourcePreference.DATASET: "Datasets"}

draft = st.session_state.pop("refine_draft", None)
if draft:
    if draft.get("mode") == "demo":
        st.session_state.update({"q_mode": "demo", "q_demo": draft["question"]})
    else:
        st.session_state.update({"q_mode": "live", "q_text": draft["question"]})
    st.session_state["q_context"] = draft["context"]
    st.session_state["parent_run_id"] = draft["parent_run_id"]

# defaults go into session state once, so widgets never get both a default and a session value
st.session_state.setdefault("q_mode", preference("mode") if user.can_use_live else "demo")
if not user.can_use_live:
    st.session_state["q_mode"] = "demo"
if st.session_state.get("q_demo") not in DEMO_QUESTIONS:
    st.session_state["q_demo"] = DEMO_QUESTIONS[0]
st.session_state.setdefault("q_depth", preference("depth"))
st.session_state.setdefault("q_iter", int(preference("max_iterations")))
st.session_state.setdefault("q_prefs", [p for p in PREF_LABELS if p != SourcePreference.DATASET])

head, action = st.columns([4, 1], vertical_alignment="center")
head.markdown(eyebrow("Your research desk") + '<div class="ds-title">What do you want to <em>understand?</em></div>'
              '<p class="ds-sub">Ask a question. The research team will take it from there.</p>',
              unsafe_allow_html=True)
if action.button("Open library", icon=":material/collections_bookmark:", width="stretch"):
    st.switch_page("app_pages/7_Research_History.py")
st.write("")
if st.session_state.get("parent_run_id"):
    st.info("Refining a previous investigation: its findings are included as background context.",
            icon=":material/replay:")

with card("composer"):
    if user.can_use_live:
        mode = st.segmented_control("Mode", ["live", "demo"], key="q_mode", required=True,
                                    format_func=lambda m: "◎ Live research" if m == "live" else "✦ Demo (sample data)")
    else:
        mode = "demo"
        html(f'{mode_tag("demo")} &nbsp;<span class="ds-muted">You are exploring as a guest. Sign in to research '
             "your own question with live sources.</span>")
    demo = mode == ResearchMode.DEMO.value

    if demo:
        html('<div class="ds-label" style="margin-top:10px">Choose a demo question</div>')
        with st.container(key="pick_demo"):
            st.radio("Demo question", DEMO_QUESTIONS, key="q_demo", captions=DEMO_CAPTIONS,
                     label_visibility="collapsed")
        st.caption("✦ Demo uses a synthetic sample dataset about AI and enterprise software, so it is limited to these "
                   "questions. " + ("Switch to Live research to ask your own." if user.can_use_live
                                    else "Sign in (sidebar) to research any question live."))
        question = st.session_state["q_demo"]
    else:
        question = st.text_area("Research question", key="q_text", height=90, max_chars=600,
                                placeholder="e.g. How might AI reshape early-stage drug discovery over the next decade?")
        st.caption(QUESTION_EVIDENCE_NOTICE)
        if problem := question_problem(question or ""):
            st.warning(problem, icon=":material/edit_note:")
        elif guidance := question_guidance(question or ""):
            st.info(guidance)
        html('<div class="ds-muted" style="margin:2px 0 6px">Need a starting point?</div>')
        for i, (col, example) in enumerate(zip(st.columns(3), DEMO_QUESTIONS)):
            col.button(example, key=f"sugg_{i}", width="stretch",
                       on_click=lambda q=example: st.session_state.update(q_text=q))

    st.divider()
    left, right = st.columns([1.7, 1], gap="large")
    with left:
        html('<div class="ds-label">Research depth</div>')

        def depth_caption(d: Depth) -> str:
            p = DEPTH_PRESETS[d]
            return (f"{p['subquestions']} threads · ≤{p['budget'].max_sources} sources"
                    + ("" if demo else f" · ~{max(1, p['budget'].max_seconds // 120)}–{p['budget'].max_seconds // 60} min"))
        with st.container(key="pick_depth"):
            depth = st.radio("Research depth", [d.value for d in Depth], key="q_depth", horizontal=True,
                             format_func=lambda d: f"{DEPTH_ICONS[d]} {DEPTH_LABELS[d]}",
                             captions=[depth_caption(d) for d in Depth], label_visibility="collapsed")
    with right:
        html('<div class="ds-label">Sources</div>')
        if demo:
            html('<div class="ds-pills"><span>✦ Synthetic demo dataset</span><span>27 sources</span><span>offline</span></div>')
        else:
            chosen = st.session_state.get("q_prefs") or []
            pills = "".join(f"<span>{PREF_LABELS[SourcePreference(p)]}</span>" for p in chosen[:3])
            more = f"<span>+{len(chosen) - 3}</span>" if len(chosen) > 3 else ""
            html(f'<div class="ds-pills">{pills}{more}</div>')
            st.caption("Change sources under Advanced research settings.")

    selected_docs: list[str] = []
    prefs: list = []
    use_dates, date_from, date_to, use_llm = False, None, None, False
    with st.expander("Advanced research settings · optional context, rounds, dates & sources"):
        context = st.text_area("Background context", key="q_context", height=90, max_chars=4000,
                               placeholder="Add objectives, constraints, or context to guide the research…",
                               help="What you already know, what decision this informs, or constraints. Used for planning.")
        objectives_text = st.text_area("Research objectives (one per line)", key="q_objectives", height=70)
        max_iter = st.slider("Maximum follow-up rounds", 1, 6, key="q_iter",
                             help="Bounded research loop: the orchestrator may stop earlier if evidence is sufficient.")
        if demo:
            st.caption("Source types, publication dates, documents and the LLM apply to live research only. Demo runs "
                       "use deterministic agents and send nothing over the network.")
        else:
            prefs = st.pills("Source types", list(PREF_LABELS), selection_mode="multi",
                             format_func=lambda p: PREF_LABELS[p], key="q_prefs")
            d1, d2, d3 = st.columns([1, 1.5, 1.5], vertical_alignment="bottom")
            use_dates = d1.checkbox("Limit publication dates", key="q_use_dates")
            date_from = d2.date_input("From", value=date(date.today().year - 3, 1, 1), disabled=not use_dates, key="q_from")
            date_to = d3.date_input("To", value=date.today(), disabled=not use_dates, key="q_to")
            if not settings.web_search_configured:
                st.caption("Web/news search is off (no `TAVILY_API_KEY`). Academic sources and uploads remain available.")
            if not settings.llm_configured:
                st.caption("No `OPENROUTER_API_KEY`: agents use deterministic logic (no LLM planning or synthesis).")
            use_llm = st.toggle("Use LLM for planning, extraction and synthesis", value=settings.llm_configured,
                                disabled=not settings.llm_configured, key="q_llm")
            st.markdown("**Your documents** (PDF, TXT, Markdown, CSV)")
            uploads = st.file_uploader("Add documents to this investigation", type=[e.lstrip(".") for e in ALLOWED_UPLOADS],
                                       accept_multiple_files=True, key=f"uploader_{st.session_state.get('up_round', 0)}")
            if uploads and st.button("Add uploaded files"):
                for file in uploads:
                    try:
                        doc = extract_upload(file.name, file.getvalue(), settings.max_upload_bytes)
                        doc_id, created = repo.save_document(file.name, doc.title, doc.kind, file.getvalue(),
                                                             doc.sections, doc.warnings, owner_id=user.id, metadata=doc.metadata)
                        if created:
                            repo.log_activity(user.id, "upload", detail={"filename": file.name})
                        st.success(f"{file.name}: {'added' if created else 'already in your library'} "
                                   f"({len(doc.sections)} sections)")
                    except ExtractionError as exc:
                        st.error(str(exc))
                st.session_state["up_round"] = st.session_state.get("up_round", 0) + 1
            library = repo.list_documents(user.id)
            if library:
                selected_docs = st.multiselect("Include these documents", [d["id"] for d in library],
                                               format_func=lambda i: next(f"{d['title'][:60]} ({d['filename']})"
                                                                          for d in library if d["id"] == i), key="q_docs")

    st.divider()
    note, start = st.columns([3, 1], vertical_alignment="center")
    note.caption("✧ Every finding is linked to its source")
    if start.button("Start research →", type="primary", width="stretch", key="start_research"):
        try:
            request = ResearchRequest(
                question=question or "", context=context, depth=Depth(depth), max_iterations=max_iter,
                mode=ResearchMode(mode), objectives=[o for o in (objectives_text or "").splitlines() if o.strip()],
                source_preferences=list(prefs or [SourcePreference.WEB, SourcePreference.ACADEMIC]),
                date_from=date_from if use_dates else None, date_to=date_to if use_dates else None, use_llm=use_llm,
                document_ids=selected_docs, parent_run_id=st.session_state.get("parent_run_id"))
        except ValidationError as exc:
            for error in exc.errors():
                if error["loc"] == ("question",) and not demo:  # already shown as a warning under the question
                    st.error("Research not started. Please correct the research question above and try again.")
                else:
                    st.error(error["msg"].removeprefix("Value error, "))
            st.stop()
        try:
            with st.spinner("Checking your question…"):  # live runs with an LLM get a sense check first
                run_id = get_run_manager().submit(request, user.id, settings, provider_settings())
        except RunRejected as exc:
            st.error(str(exc), icon=":material/hourglass_top:")
            st.stop()
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))
            st.stop()
        st.session_state.pop("parent_run_id", None)
        open_progress(run_id)

# ------------------------------------------------------------------ recent investigations
st.write("")
head, link = st.columns([4, 1], vertical_alignment="bottom")
head.markdown("### Recent investigations\n<span class='ds-muted'>Pick up where you left off</span>",
              unsafe_allow_html=True)
if link.button("View all →", key="view_all", width="stretch"):
    st.switch_page("app_pages/7_Research_History.py")
runs = repo.list_runs(user.id, limit=3)
if not runs:
    st.caption("No investigations yet. " + ("Pick a demo question above to see the agents at work."
                                            if user.is_guest else "Start one above."))
ICONS = [("▤", "ds-purple"), ("✧", "ds-pink"), ("⌁", "ds-blue")]
for i, (col, run) in enumerate(zip(st.columns(3), runs)):
    icon, css = ICONS[i % 3]
    with col, card(f"recent-{run.id}"):
        html(f'<div style="display:flex;justify-content:space-between;align-items:center"><span class="ds-icon {css}">'
             f'{icon}</span><span>{mode_tag(run.mode)} {status_tag(run.status)}</span></div>')
        st.markdown(f"**{run.question}**")
        st.caption(f"{run.counts.get('sources', 0)} sources · {DEPTH_LABELS.get(run.depth, 'Standard')} research · "
                   f"{ago(run.created_at)}")
        live = run.status in ACTIVE_STATUSES
        if st.button("Follow live" if live else "Open", key=f"open_{run.id}", width="stretch"):
            if live:
                open_progress(run.id)
            else:
                open_run(run.id)
