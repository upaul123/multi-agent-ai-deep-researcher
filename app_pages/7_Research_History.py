"""Page 7: Research library. The current user's own investigations and activity (private per user)."""
import re

import pandas as pd
import streamlit as st

from src.ui.components import DEPTH_LABELS, ago, card, eyebrow, mode_tag, status_tag, strip_citations
from src.models.run import ACTIVE_STATUSES
from src.ui.state import (current_user, get_repo, new_research, open_progress, open_run, rerun_button,
                          selected_run_id)

repo, user = get_repo(), current_user()
head, action = st.columns([4, 1], vertical_alignment="center")
head.markdown(eyebrow("Your knowledge base") + '<div class="ds-title">Research <em>library</em></div>'
              '<p class="ds-sub">' + ("All your investigations, private to your account and ready when you need them."
                                      if user.auth_enabled else "All your investigations, ready when you need them.")
              + "</p>", unsafe_allow_html=True)
if action.button("New research", icon=":material/add:", type="primary", width="stretch", key="lib_new"):
    new_research()
if user.is_guest:
    st.info("You're browsing as a guest: demo runs from this session are listed below and are removed after 24 hours. "
            "Sign in to keep a private, permanent history.", icon=":material/info:")

tab_runs, tab_activity = st.tabs(["My research", "My activity"])

with tab_runs:
    runs = repo.list_runs(user.id, limit=200)
    if not runs:
        st.info("No research runs yet.", icon=":material/info:")
    else:
        with card("lib-controls"):
            s1, s2 = st.columns([3, 1])
            search = s1.text_input("Search investigations", placeholder="⌕  Search investigations…",
                                   label_visibility="collapsed")
            sort = s2.selectbox("Sort", ["Recently updated", "Oldest first", "A–Z"], label_visibility="collapsed")
            groups = {"completed": ("completed",), "running": ACTIVE_STATUSES, "cancelled": ("cancelled",),
                      "failed": ("failed",)}
            counts = {"all": len(runs), **{g: sum(r.status in members for r in runs) for g, members in groups.items()}}
            names = {"all": "All research", "completed": "Completed", "running": "In progress", "cancelled": "Stopped",
                     "failed": "Failed"}
            status_filter = st.segmented_control("Status", list(names), default="all", label_visibility="collapsed",
                                                 format_func=lambda s: f"{names[s]} · {counts[s]}") or "all"
        runs = [r for r in runs if (status_filter == "all" or r.status in groups[status_filter])
                and (not search or search.lower() in r.question.lower())]
        if sort == "Oldest first":
            runs = runs[::-1]
        elif sort == "A–Z":
            runs = sorted(runs, key=lambda r: r.question.lower())
        widths = [3.3, 1.0, 0.8, 0.7, 1.0, 1.6]
        cols = st.columns(widths)
        for col, title in zip(cols, ["INVESTIGATION", "STATUS", "DEPTH", "SOURCES", "UPDATED", ""]):
            col.markdown(f'<div class="ds-colhead">{title}</div>', unsafe_allow_html=True)
        current = selected_run_id()
        for run in runs:
            with card(f"run-{run.id}"):
                c = st.columns(widths, vertical_alignment="center")
                marker = "● " if run.id == current else ""
                c[0].markdown(f"**{marker}{run.question}**  \n<span class='dr-muted'>{mode_tag(run.mode)} "
                              f"{run.counts.get('claims', 0)} claims · {run.counts.get('contradictions', 0)} "
                              f"contradictions{' · refines ' + run.parent_run_id if run.parent_run_id else ''}</span>",
                              unsafe_allow_html=True)
                c[1].markdown(status_tag(run.status), unsafe_allow_html=True)
                c[2].markdown(f'<div class="ds-cell">{DEPTH_LABELS.get(run.depth, "—")}</div>', unsafe_allow_html=True)
                c[3].markdown(f'<div class="ds-cell">{run.counts.get("sources", 0)}</div>', unsafe_allow_html=True)
                c[4].markdown(f'<div class="ds-cell">{ago(run.created_at)}</div>', unsafe_allow_html=True)
                b1, b2 = c[5].columns([1.5, 1])
                live = run.status in ACTIVE_STATUSES
                if run.status == "failed":  # interrupted or failed: offer to start it again
                    with b1:
                        rerun_button(run.id, key=f"rerun_{run.id}")
                elif b1.button("Follow" if live else "Open", key=f"open_{run.id}"):
                    if live:
                        open_progress(run.id)
                    else:
                        open_run(run.id)
                with b2.popover("", icon=":material/more_horiz:"):
                    if run.status == "failed" and st.button("Open details", key=f"open_{run.id}"):
                        open_progress(run.id)
                    if run.summary:
                        st.markdown("**Summary of findings**")
                        # Inline " - **Finding...**" items are put on their own lines as a bulleted list.
                        summary = strip_citations(run.summary)
                        st.markdown(re.sub(r"\s+-\s+(?=\*\*)", "\n- ", summary)[:1200])
                    if live:
                        st.write("This investigation is still in progress. Stop it from its live view before "
                                 "deleting it.")
                    else:
                        st.write("Delete this run and all of its stored sources, evidence, report and Q&A?")
                    if st.button("Delete", key=f"del_{run.id}", type="primary", disabled=live):
                        if repo.delete_run(run.id, user.id):
                            repo.log_activity(user.id, "run_deleted", run.id, {"question": run.question[:200]})
                        if current == run.id:
                            st.session_state.pop("selected_run_id", None)
                        st.session_state.pop("_bundle_cache", None)
                        st.rerun()
        st.caption(f"Showing {len(runs)} of {counts['all']} investigations")

LABELS = {"login": "Signed in", "run_started": "Started research", "run_completed": "Research completed",
          "run_failed": "Research failed", "run_rerun": "Re-ran research", "run_cancelled": "Stopped research", "profile_updated": "Updated profile", "run_deleted": "Deleted a run", "qa": "Asked a follow-up question",
          "export": "Exported a report", "upload": "Uploaded a document", "data_deleted": "Deleted all data"}

with tab_activity:
    activity = repo.list_activity(user.id)
    if not activity:
        st.caption("No activity recorded yet.")
    else:
        st.dataframe(pd.DataFrame([{
            "When": a["created_at"].replace("T", " ")[:16], "Activity": LABELS.get(a["kind"], a["kind"]),
            "Details": "; ".join(f"{k}: {v}" for k, v in a["detail"].items()), "Run": a["run_id"] or ""}
            for a in activity]), hide_index=True, width="stretch")

if not user.is_guest:
    st.divider()
    with st.expander("Delete my data", icon=":material/delete_forever:"):
        st.write("Permanently delete **all** of your research runs (with their evidence and reports), uploaded "
                 "documents, activity history and preferences. This cannot be undone.")
        confirm = st.text_input("Type DELETE to confirm", key="confirm_delete_all")
        if st.button("Delete everything", type="primary", disabled=confirm != "DELETE"):
            count = repo.delete_user_data(user.id)
            repo.log_activity(user.id, "data_deleted", detail={"runs": count})
            for key in ("selected_run_id", "_bundle_cache"):
                st.session_state.pop(key, None)
            st.success(f"Deleted {count} run(s) and all associated data.")
            st.rerun()
