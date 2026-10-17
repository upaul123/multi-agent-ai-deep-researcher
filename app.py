"""Deep Researcher AI: Streamlit entry point.

Run:  streamlit run app.py

Visitors who are not signed in see a minimal public home (Try Demo or Sign in). Signed-in users (and the local user)
land on their research home: question composer, the agents team, the workflow graph and recent investigations.
"""
import logging
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import streamlit as st
from pydantic import ValidationError

from src.config import APP_NAME
from src.models.research import DEMO_QUESTIONS, DEPTH_PRESETS, Depth, ResearchMode, ResearchRequest, SourcePreference
from src.models.research import QUESTION_EVIDENCE_NOTICE, question_guidance, question_problem
from src.models.run import ACTIVE_STATUSES
from src.services.jobs import RunRejected
from src.ui.brand import agents_team_html, landing_figure_html, public_hero_html, workflow_graph_html
from src.ui.components import DEPTH_LABELS, ago, card, html, inject_css, mode_tag, status_tag
from src.ui.state import (LOGO, PROFILE_PAGE, SIGN_IN_PAGE, current_user, effective_settings, get_repo,
                          get_run_manager, open_progress, open_run, preference, provider_settings, sidebar)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
st.set_page_config(page_title=APP_NAME, page_icon=LOGO, layout="wide")
WORKSPACE = "app_pages/1_Research_Workspace.py"


def try_demo() -> None:
    """Open the workspace in demo mode with the first sample question selected."""
    for key in [k for k in st.session_state if k.startswith("q_")]:
        st.session_state.pop(key, None)
    st.session_state.update({"q_mode": "demo", "q_demo": DEMO_QUESTIONS[0]})


def public_home() -> None:
    """Minimal: who we are, what it does, Try Demo or Sign in."""
    left, right = st.columns([1, 1.05], gap="large", vertical_alignment="center")
    with left:
        html('<div class="dr-chip-row"><span class="dr-eyebrow-chip"><span class="dr-live-dot"></span>'
             'Multi-agent research assistant</span></div>'
             '<div class="dr-h1">Ask a hard question.<br><em>Get a cited answer.</em></div>'
             '<p class="dr-lead">A team of eight specialist AI agents plans the investigation, searches academic and '
             "web sources, checks every claim against the passage it came from, and writes a report you can verify.</p>")
        with st.container(horizontal=True):
            if st.button("Try Demo", icon=":material/arrow_forward:", icon_position="right", type="primary",
                         key="hero_demo", on_click=try_demo):
                st.switch_page(WORKSPACE)
            if st.button("Sign in", key="hero_signin"):
                st.switch_page(SIGN_IN_PAGE)
        st.caption("The demo runs on a labelled synthetic dataset. No account needed.")
    with right:
        st.html(public_hero_html())
    st.divider()
    st.caption(f"{APP_NAME} · A citation shows where a claim came from, not that it is true. Check important "
               "conclusions against the original sources.")


def greeting(name: str) -> str:
    try:
        hour = datetime.now(ZoneInfo(st.context.timezone)).hour if st.context.timezone else datetime.now().hour
    except Exception:  # unknown or missing browser timezone
        hour = datetime.now().hour
    part = "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 18 else "evening"
    first = name.split()[0] if name and name not in ("Local user", "Signed-in user") else ""
    return f"Good {part}" + (f", {first}" if first else "")


def start_from_home(user) -> None:
    question = (st.session_state.get("home_q") or "").strip()
    depth = st.session_state.get("home_depth") or preference("depth")
    try:
        request = ResearchRequest(
            question=question, depth=Depth(depth), max_iterations=int(preference("max_iterations")),
            mode=ResearchMode.LIVE, use_llm=effective_settings().llm_configured,
            source_preferences=[p for p in SourcePreference if p != SourcePreference.DATASET])
        run_id = get_run_manager().submit(request, user.id, effective_settings(), provider_settings())
    except ValidationError as exc:
        st.session_state["home_error"] = "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
        return
    except (RunRejected, PermissionError, ValueError) as exc:
        st.session_state["home_error"] = str(exc)
        return
    st.session_state["home_started"] = run_id


def advanced_from_home() -> None:
    for key in [k for k in st.session_state if k.startswith("q_")]:
        st.session_state.pop(key, None)
    st.session_state.update({"q_mode": "live", "q_text": st.session_state.get("home_q", "")})


def landing() -> None:
    user = current_user()
    html(f'<div class="dr-greet"><h1>{escape(greeting(user.name))}</h1>'
         "<p>Your research team is ready. Ask a question to start an investigation.</p></div>")
    st.write("")
    composer, figure = st.columns([3, 2], gap="medium")
    with composer, card("composer-home"):
        st.text_area("What do you want to understand?", key="home_q", height=110, max_chars=600,
                     placeholder="e.g. How might AI reshape early-stage drug discovery over the next decade?")
        st.caption(QUESTION_EVIDENCE_NOTICE)
        if "home_error" in st.session_state:
            pass  # a rejected submission shows its own error below the buttons
        elif problem := question_problem(st.session_state.get("home_q", "")):
            st.warning(problem, icon=":material/edit_note:")
        elif guidance := question_guidance(st.session_state.get("home_q", "")):
            st.info(guidance)
        st.segmented_control(
            "Research depth", [d.value for d in Depth], key="home_depth", default=preference("depth"),
            format_func=lambda d: f"{DEPTH_LABELS[d]} · ~{max(1, DEPTH_PRESETS[Depth(d)]['budget'].max_seconds // 60)} min")
        with st.container(horizontal=True, horizontal_alignment="right"):
            if st.button("Advanced", key="home_advanced", on_click=advanced_from_home):
                st.switch_page(WORKSPACE)
            st.button("Start research", icon=":material/arrow_forward:", icon_position="right", type="primary",
                      key="home_start", on_click=start_from_home, args=(user,))
        if error := st.session_state.pop("home_error", None):
            st.error(error)
        if run_id := st.session_state.pop("home_started", None):
            open_progress(run_id)
    with figure:
        st.html(landing_figure_html())

    st.write("")
    st.html(agents_team_html())
    st.write("")
    st.html(workflow_graph_html())

    st.write("")
    head, link = st.columns([4, 1], vertical_alignment="bottom")
    head.subheader("Recent investigations")
    if link.button("Open library", key="home_library", width="stretch"):
        st.switch_page("app_pages/7_Research_History.py")
    runs = get_repo().list_runs(user.id, limit=3)
    if not runs:
        st.caption("No investigations yet. Ask your first question above.")
    for col, run in zip(st.columns(3), runs):
        live = run.status in ACTIVE_STATUSES
        with col, card(f"home-run-{run.id}"):
            html(f"{status_tag(run.status)} {mode_tag(run.mode)}")
            st.markdown(f"**{run.question}**")
            st.caption(f"{DEPTH_LABELS.get(run.depth, 'Standard')} · {run.counts.get('sources', 0)} sources · "
                       f"{ago(run.created_at)}")
            if st.button("Follow live" if live else "Open", key=f"home_open_{run.id}", width="stretch"):
                if live:
                    open_progress(run.id)
                else:
                    open_run(run.id)


def home() -> None:
    if current_user().is_guest:
        public_home()
    else:
        landing()


inject_css()
get_repo()
visitor = current_user()
home_page = st.Page(home, title="Home", icon=":material/home:", default=True)
research_pages = [
    st.Page(WORKSPACE, title="Try the demo" if visitor.is_guest else "Research workspace", icon=":material/travel_explore:",
            url_path="workspace"),
    st.Page("app_pages/2_Research_Progress.py", title="Agents in action", icon=":material/motion_photos_on:",
            url_path="progress"),
    st.Page("app_pages/3_Research_Results.py", title="Research results", icon=":material/article:", url_path="results"),
    st.Page("app_pages/7_Research_History.py", title="Research library", icon=":material/collections_bookmark:",
            url_path="library"),
]
settings_page = st.Page("app_pages/8_Settings.py", title="Settings", icon=":material/settings:", url_path="settings")
if visitor.is_guest:
    account = [st.Page(SIGN_IN_PAGE, title="Sign in", icon=":material/login:", url_path="sign-in"), settings_page]
else:
    account = [st.Page(PROFILE_PAGE, title="Update profile", icon=":material/person:", url_path="profile"), settings_page]
navigation = st.navigation({"": [home_page], "Research": research_pages, "Account": account})
sidebar()
navigation.run()
