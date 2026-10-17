"""Page 2: Agents in action. Live progress of a queued/running investigation, or the audit trail of a past run.

Research runs in the process-wide job runner, not in this page. The live view polls the database, so leaving the
page, refreshing or opening the app in another tab or device reattaches to the same run.
"""
from html import escape

import pandas as pd
import streamlit as st

from src.models.evidence import AgentEvent
from src.orchestration.state import AGENT_ORDER
from src.services.run_events import ProgressTracker
from src.ui.components import AGENT_INFO, AGENT_TITLES, DEPTH_LABELS, card, demo_banner, eyebrow, html
from src.ui.state import (active_run_id, current_user, get_repo, get_run_manager, load_selected_run, new_research,
                          open_run, rerun_button, select_run)

repo = get_repo()
POLL_SECONDS = 1.5
MAX_FEED_EVENTS = 50  # the activity card shows the latest few; keep the session's copy small
STATE_LABEL = {"completed": "✓ Complete", "running": "◌ Working", "failed": "✕ Failed"}
ROW_CLASS = {"completed": "done", "running": "working", "failed": "failed"}


def heading(live: bool) -> None:
    left, right = st.columns([4, 1], vertical_alignment="center")
    left.markdown(eyebrow("Live investigation" if live else "Investigation log")
                  + '<div class="ds-title">Agents in <em>action</em></div><p class="ds-sub">'
                  + ("Your research team is working through the evidence." if live else
                     "What each agent did during this investigation, step by step.") + "</p>",
                  unsafe_allow_html=True)
    if live:
        right.markdown('<div style="text-align:right"><span class="ds-live"><i></i> LIVE</span></div>',
                       unsafe_allow_html=True)


def question_card(question: str, meta: list[str], done: int, caption: str) -> str:
    pct = int(round(100 * done / len(AGENT_ORDER)))
    spans = "".join(f"<span>{escape(m)}</span>" for m in meta)
    return (f'<div class="ds-card"><div class="ds-label">Research question</div>'
            f'<div style="font:600 17px Manrope,sans-serif;margin:6px 0 9px">{escape(question)}</div>'
            f'<div class="ds-meta">{spans}</div><div class="ds-progress"><i style="width:{pct}%"></i></div>'
            f'<div class="ds-progress-cap"><span>{escape(caption)}</span><b>{pct}%</b></div></div>')


def agents_card(statuses: dict[str, str], messages: dict[str, str], extra: dict[str, str] | None = None) -> str:
    rows = []
    for agent in AGENT_ORDER:
        icon, css, about = AGENT_INFO[agent]
        status = statuses.get(agent, "pending")
        note = messages.get(agent) or about
        state = (extra or {}).get(agent) or STATE_LABEL.get(status, "Queued")
        rows.append(f'<div class="ds-agent {ROW_CLASS.get(status, "")}"><span class="ds-icon {css}">{icon}</span>'
                    f'<div><b>{AGENT_TITLES[agent]}</b><small>{escape(note[:140])}</small></div>'
                    f'<span class="ds-state">{escape(state)}</span></div>')
    return (f'<div class="ds-card"><div style="display:flex;justify-content:space-between;align-items:center">'
            f'<b style="font:700 15px Manrope,sans-serif">Research team</b><span class="ds-muted">{len(AGENT_ORDER)} '
            f'agents</span></div>{"".join(rows)}</div>')


def activity_card(events: list[AgentEvent], running: bool) -> str:
    shown = [e for e in events if e.status not in ("search", "started")][-7:] or events[-7:]
    lines = []
    for i, e in enumerate(shown):
        current = " current" if running and i == len(shown) - 1 else ""
        title = f"{AGENT_TITLES.get(e.agent, e.agent)} · {e.status}"
        lines.append(f'<div class="ds-act{current}"><i></i><div><b>{escape(title)}</b>'
                     f'<small>{escape(e.message[:150])}</small><time>{e.timestamp:%H:%M:%S}</time></div></div>')
    body = "".join(lines) or '<div class="ds-muted" style="padding:10px 0">Waiting for the first agent…</div>'
    return (f'<div class="ds-card"><div style="display:flex;justify-content:space-between">'
            f'<b style="font:700 15px Manrope,sans-serif">Research activity</b>'
            f'<span style="color:#55b592">●</span></div>{body}</div>')


@st.fragment(run_every=POLL_SECONDS)
def live_view(run_id: str) -> None:
    """Redraws from the database every few seconds; only events newer than the last poll are fetched."""
    feed = st.session_state.get("_live_feed")
    if not feed or feed["run_id"] != run_id:
        feed = st.session_state["_live_feed"] = {"run_id": run_id, "after": -1, "events": []}
    progress = repo.get_progress(run_id, user.id, after_seq=feed["after"])
    if progress is None:
        st.warning("This investigation is no longer available.", icon=":material/info:")
        return
    feed["events"] = (feed["events"] + progress.events)[-MAX_FEED_EVENTS:]
    feed["after"] = progress.last_seq
    if not progress.active:  # finished: switch the whole page to the audit trail
        st.session_state.pop("_live_feed", None)
        st.rerun()
    tracker = ProgressTracker.from_snapshot(progress.progress)
    meta = [f"✧ {DEPTH_LABELS.get(progress.depth, 'Standard')} research",
            "Demo dataset" if progress.mode == "demo" else "Live sources",
            f"Started {progress.created_at:%H:%M}", f"{tracker.sources} sources found"]
    if progress.cancel_requested:
        caption = "Stopping: writing the report from the evidence gathered so far"
    elif progress.status == "queued":
        position = get_run_manager().queue_position(run_id)
        caption = "Waiting for a free research slot" + (f" (position {position} in the queue)" if position else "")
    else:
        caption = f"{AGENT_TITLES[tracker.current]} working" if tracker.current else "Starting the research team"
    html(question_card(progress.question, meta, tracker.done, caption))
    left, right = st.columns([1.5, 0.85])
    left.markdown(agents_card(tracker.statuses, tracker.messages), unsafe_allow_html=True)
    right.markdown(activity_card(feed["events"], running=True), unsafe_allow_html=True)
    note, stop = st.columns([3, 1], vertical_alignment="center")
    note.caption("You can leave this page: the investigation keeps running, and you can come back from the sidebar.")
    # a callback runs before the next render, so the redraw already shows the stop (no explicit rerun needed)
    stop.button("Stop & write report", icon=":material/stop_circle:", key="stop_run", width="stretch",
                disabled=progress.cancel_requested, on_click=get_run_manager().cancel, args=(run_id, user.id),
                help="Stop searching and write the report from the evidence gathered so far.")


user = current_user()
run_id = st.session_state.get("selected_run_id") or active_run_id(user.id)
current = repo.get_progress(run_id, user.id) if run_id else None
if current is not None and current.active:
    select_run(run_id)
    heading(live=True)
    demo_banner(current.mode)
    live_view(run_id)
    st.stop()

# ------------------------------------------------------------------------------ audit trail of the selected run
bundle = load_selected_run()
heading(live=False)
if bundle is None:
    st.info("No investigation is running. Start one in the Research workspace.", icon=":material/info:")
    if st.button("New research", icon=":material/add:", type="primary"):
        new_research()
    st.stop()

demo_banner(bundle.summary.mode)
statuses = {a: s.status for a, s in bundle.statuses.items()}
messages: dict[str, str] = {}
for event in bundle.events:
    if event.agent in AGENT_ORDER and event.status != "started":
        messages[event.agent] = event.message
extra = {a: f"✓ {s.runs}× · {s.duration_ms} ms" for a, s in bundle.statuses.items() if s.status == "completed"}
usage = bundle.usage
meta = [f"✧ {DEPTH_LABELS.get(bundle.summary.depth, 'Standard')} research",
        "Demo dataset" if bundle.summary.mode == "demo" else "Live sources",
        f"Started {bundle.summary.created_at:%d %b %Y %H:%M}", f"{len(bundle.sources)} sources found"]
html(question_card(bundle.summary.question, meta, sum(s == "completed" for s in statuses.values()),
                   f"Research {bundle.summary.status}" + (f" in {usage.elapsed_seconds}s" if usage else "")))
st.write("")
left, right = st.columns([1.5, 0.85])
left.markdown(agents_card(statuses, messages, extra), unsafe_allow_html=True)
right.markdown(activity_card(bundle.events, running=False), unsafe_allow_html=True)
st.write("")
found = (f"{len(bundle.sources)} sources, {len(bundle.claims)} claims, {len(bundle.contradictions)} contradiction(s), "
         f"{len(bundle.insights)} insights")
if bundle.summary.status == "completed":
    st.success(f"Research complete: {found}.", icon=":material/check_circle:")
elif bundle.summary.status == "cancelled":
    st.info(f"Stopped by you: {found}. "
            + ("The report covers only the evidence gathered before stopping." if bundle.report else
               "It was stopped before it started."), icon=":material/stop_circle:")
elif bundle.summary.status == "failed":
    st.error("This investigation did not finish. You can re-run it with the same question and settings; the "
             "details of what went wrong are below.", icon=":material/error:")
if usage:
    with card("usage"):
        m = st.columns(7)
        m[0].metric("Rounds", usage.iterations + 1)
        m[1].metric("Searches", usage.searches)
        m[2].metric("Cache hits", usage.cache_hits)
        m[3].metric("Sources", len(bundle.sources))
        m[4].metric("Claims", len(bundle.claims))
        m[5].metric("API attempts", usage.llm_attempts, help=f"{usage.llm_calls} logical calls; includes retries")
        m[6].metric("Duration", f"{usage.elapsed_seconds}s")
        st.caption(f"Observed tokens: {usage.llm_tokens:,} · Unreported token allowance charged: "
                   f"{usage.llm_unreported_tokens:,} · In-flight allowance: {usage.llm_reserved_tokens:,}")
        if usage.cost_estimate_complete:
            st.caption(f"Estimated model cost: ${usage.estimated_cost_usd:.6f} (configured rates; not an invoice).")
        elif usage.llm_attempts:
            st.caption("Model cost is unavailable or partial: configure input/output prices and review provider billing.")
        st.markdown(f"**Stopping decision:** {bundle.stop_reason or '—'}")
        if usage.exhausted:
            st.warning("Limits reached: " + ", ".join(usage.exhausted), icon="⏱️")
for note in bundle.provider_notes:
    st.caption(f"ℹ️ {note}")

with st.expander("Full event log"):
    st.dataframe(pd.DataFrame([{"Time": e.timestamp.strftime("%H:%M:%S"), "Agent": AGENT_TITLES.get(e.agent, e.agent),
                                "Event": e.status, "Round": e.iteration, "Message": e.message} for e in bundle.events]),
                 hide_index=True, width="stretch", height=420)
with st.expander("Search activity"):
    if bundle.search_log:
        st.dataframe(pd.DataFrame([{"Round": r["iteration"], "Subquestion": r.get("subquestion_id") or "—",
                                    "Provider": r["provider"], "Query": r["query"], "Status": r["status"],
                                    "Results": r["results"], "New sources": r["new_sources"], "Note": r.get("message", "")}
                                   for r in bundle.search_log]), hide_index=True, width="stretch")
    else:
        st.caption("No searches were recorded.")
for warning in dict.fromkeys(bundle.warnings):
    st.warning(warning)
for error in bundle.errors:
    st.error(error)

a, r, b = st.columns([2, 1, 1])
if a.button("New research", icon=":material/add:", key="log_new"):
    new_research()
if bundle.summary.status == "failed":
    with r:
        rerun_button(bundle.summary.id, key="log_rerun", width="stretch")
if b.button("View results →", type="primary", width="stretch", key="log_results"):
    open_run(bundle.summary.id)
