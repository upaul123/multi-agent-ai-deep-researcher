"""UI identity modes: local single user (no [auth]) and guest (sign-in configured, not signed in)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")
AUTH_SECRETS = {"redirect_uri": "http://localhost:8501/oauth2callback", "cookie_secret": "test-cookie-secret",
                "google": {"client_id": "test-client", "client_secret": "test-secret",
                           "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration"}}


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    from src.config import get_settings
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")  # never call a real LLM (e.g. the submit-time question check)
    get_settings.cache_clear()
    import streamlit as st
    from streamlit import config
    # Ignore the developer's real .streamlit/secrets.toml: each test sets exactly the secrets it needs.
    old_files = config.get_option("secrets.files")
    config.set_option("secrets.files", [str(tmp_path / "no-secrets.toml")])
    st.secrets._secrets = None
    st.cache_resource.clear()
    yield
    config.set_option("secrets.files", old_files)
    st.secrets._secrets = None
    get_settings.cache_clear()
    st.cache_resource.clear()


def app(with_auth: bool) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    if with_auth:
        at.secrets["auth"] = AUTH_SECRETS
    return at.run()


def start_demo(at: AppTest, question_index: int = 0) -> AppTest:
    from src.models.research import DEMO_QUESTIONS
    at.switch_page("app_pages/1_Research_Workspace.py").run()
    if at.segmented_control:  # users who may run live research choose Demo first; guests are always in demo
        at.segmented_control(key="q_mode").set_value("demo").run()
    at.radio(key="q_demo").set_value(DEMO_QUESTIONS[question_index]).run()
    at.button(key="start_research").click().run()
    return wait_for_run(at)


def wait_for_run(at: AppTest, timeout: float = 120) -> AppTest:
    """The run executes in the background job runner: re-render the progress page until it shows the outcome."""
    import time
    deadline = time.monotonic() + timeout
    while not (at.success or at.exception or [e for e in at.error]) and time.monotonic() < deadline:
        time.sleep(0.2)
        at.switch_page("app_pages/2_Research_Progress.py").run()  # AppTest reruns its own page, not the app's switch
    return at


def test_local_mode_is_single_admin_user():
    at = app(with_auth=False)
    assert not at.exception
    assert not [b for b in at.button if "Sign in" in b.label]  # no login without [auth]
    at.switch_page("app_pages/8_Settings.py").run()
    assert not at.exception and any("App-wide settings" in s.value for s in at.subheader)  # local user is admin


def test_guest_gets_sign_in_and_demo_only():
    at = app(with_auth=True)
    assert not at.exception
    assert at.button(key="hero_demo") and at.button(key="hero_signin")  # minimal public home: demo or sign in
    assert not [t for t in at.text_area if t.key == "home_q"]  # no research composer for guests
    at.switch_page("app_pages/1_Research_Workspace.py").run()
    assert not at.exception
    assert not at.segmented_control  # no Live / Demo switch for guests
    assert not [t for t in at.text_area if t.key == "q_text"]  # no free-text question in demo mode
    assert at.radio(key="q_demo").options == ["How will agentic AI transform enterprise software over the next five years?",
                                              "Which emerging technologies could create new business opportunities in India?",
                                              "What evidence supports or contradicts the claim that AI agents will replace "
                                              "traditional SaaS workflows?"]
    assert any("Sign in (sidebar) to research any question live" in c.value for c in at.caption)
    at.switch_page("app_pages/8_Settings.py").run()
    assert any("Sign in to save personal preferences" in i.value for i in at.info)
    assert not any("App-wide settings" in s.value for s in at.subheader)  # guests never see admin settings


def test_signed_in_style_user_can_switch_to_live_with_free_text():
    at = app(with_auth=False)  # local user can use live, like a signed-in user
    at.switch_page("app_pages/1_Research_Workspace.py").run()
    at.segmented_control(key="q_mode").set_value("live").run()
    assert not at.exception and [t for t in at.text_area if t.key == "q_text"]
    at.segmented_control(key="q_mode").set_value("demo").run()
    assert not [t for t in at.text_area if t.key == "q_text"] and at.radio(key="q_demo")


def test_demo_run_renders_every_screen():
    at = start_demo(app(with_auth=False), question_index=2)
    assert not at.exception and at.success  # agents in action: finished
    for page in ("app_pages/3_Research_Results.py", "app_pages/2_Research_Progress.py", "app_pages/7_Research_History.py",
                 "app_pages/8_Settings.py"):  # the Welcome page already rendered when the app started
        at.switch_page(page).run()
        assert not at.exception, page
    at.switch_page("app_pages/3_Research_Results.py").run()
    assert len(at.tabs) == 7 and at.tabs[0].label == "Overview"


def test_guest_demo_run_is_listed_only_for_that_guest():
    from src.models.research import DEMO_QUESTIONS
    at = start_demo(app(with_auth=True))
    assert not at.exception and at.success
    at.switch_page("app_pages/7_Research_History.py").run()
    assert not at.exception and any("guest" in i.value.lower() for i in at.info)
    assert any(DEMO_QUESTIONS[0] in m.value for m in at.markdown)  # this guest sees their own demo run
    other = app(with_auth=True)  # a different browser session = a different guest
    other.switch_page("app_pages/7_Research_History.py").run()
    assert any("No research runs yet" in i.value for i in other.info)


def test_live_view_reattaches_and_stops_a_running_investigation(tmp_path):
    """A run executing elsewhere (another tab, device or instance) is followed from the database and can be stopped."""
    from src.database.repository import LOCAL_OWNER, Repository
    from src.models.research import ResearchRequest
    from src.models.evidence import AgentEvent
    from tests.conftest import QUESTION
    at = app(with_auth=False)
    repo = Repository(tmp_path / "ui.db")
    repo.create_run("run_elsewhere", ResearchRequest(question=QUESTION), LOCAL_OWNER, worker_id="other-instance")
    repo.update_progress("run_elsewhere", LOCAL_OWNER, {"statuses": {"planner": "completed", "retriever": "running"},
                                                        "messages": {"planner": "Plan ready"}, "sources": 3},
                         [AgentEvent(agent="retriever", status="search", message="Searching the corpus")], 0)

    at.run()  # the sidebar lists it as in progress
    assert any(b.key == "active_run_elsewhere" for b in at.button)
    at.switch_page("app_pages/2_Research_Progress.py").run()  # no selection: the active run is found
    assert not at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "Contextual Retriever working" in page and "3 sources found" in page and "Searching the corpus" in page
    at.button(key="stop_run").click().run()
    assert not at.exception and repo.is_cancel_requested("run_elsewhere")
    assert at.button(key="stop_run").disabled and "Stopping" in " ".join(m.value for m in at.markdown)

    at.switch_page("app_pages/3_Research_Results.py").run()  # no half-finished results
    assert any("still in progress" in i.value for i in at.info) and not at.tabs
    at.switch_page("app_pages/7_Research_History.py").run()
    assert not at.exception and any(b.label == "Follow" for b in at.button)
    assert any(b.key == "del_run_elsewhere" and b.disabled for b in at.button)  # stop before deleting


def test_second_run_is_refused_while_one_is_active(tmp_path):
    from src.database.repository import LOCAL_OWNER, Repository
    from src.models.research import ResearchRequest
    from tests.conftest import QUESTION
    at = app(with_auth=False)
    Repository(tmp_path / "ui.db").create_run("run_busy", ResearchRequest(question=QUESTION), LOCAL_OWNER)
    at.switch_page("app_pages/1_Research_Workspace.py").run()
    at.segmented_control(key="q_mode").set_value("demo").run()
    at.button(key="start_research").click().run()
    assert any("already have an investigation in progress" in e.value for e in at.error)


# ------------------------------------------------------------------ redesigned home, sign-in and profile
ALL_PROVIDERS = {**AUTH_SECRETS,
                 "microsoft": {"client_id": "ms", "client_secret": "ms-secret",
                               "server_metadata_url": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"},
                 "email": {"client_id": "a0", "client_secret": "a0-secret",
                           "server_metadata_url": "https://example-tenant.eu.auth0.com/.well-known/openid-configuration"}}


def test_signed_in_landing_shows_team_graph_and_composer():
    at = app(with_auth=False)  # the local user lands on the signed-in home
    assert not at.exception
    assert at.text_area(key="home_q") and at.button(key="home_start")
    page = " ".join(h.proto.body for h in at.get("html"))
    assert "Get to know your Agents team" in page and page.count('<article class="dr-tile') == 8
    assert "How an investigation flows" in page and "run side by side" in page  # parallel branch is shown
    assert page.count('<article class="dr-ctile') == 2  # cross-cutting agents, separate from the pipeline tiles
    assert "Observability &amp; Telemetry" in page and "Cost Optimizer" in page
    assert "4 · Cross-cutting · always on" in page
    assert page.count('class="dr-abs dr-tap') == 7  # every step feeds the always-on lane
    assert "Model routing" not in page  # only capabilities the app really has
    assert "app/static/hero.jpg" in page


def test_landing_starts_research_in_the_background():
    at = app(with_auth=False)
    at.text_area(key="home_q").input("What evidence links sleep duration to cognitive performance in adults?")
    at.button(key="home_start").click().run()
    assert not at.exception and not at.error
    from src.ui.state import get_repo
    [run] = get_repo().list_runs("local", limit=5)
    assert run.mode == "live" and run.question.startswith("What evidence links sleep")
    from src.ui.state import get_run_manager
    assert get_run_manager().wait(run.id, 120)


def test_sign_in_page_lists_configured_providers_and_create_account():
    at = AppTest.from_file(APP, default_timeout=120)
    at.secrets["auth"] = ALL_PROVIDERS
    at.run()
    at.switch_page("app_pages/0_Sign_in.py").run()
    assert not at.exception
    labels = [b.label for b in at.button if b.key and b.key.startswith("login_")]
    assert labels == ["Continue with Google", "Continue with Microsoft", "Continue with email"]
    at.segmented_control(key="signin_mode").set_value("Create account").run()
    labels = [b.label for b in at.button if b.key and b.key.startswith("login_")]
    assert labels == ["Sign up with Google", "Sign up with Microsoft", "Sign up with email"]
    assert any("never sees your password" in c.value for c in at.caption)


def test_profile_is_saved_validated_and_private():
    at = app(with_auth=False)
    at.switch_page("app_pages/9_Profile.py").run()
    assert not at.exception
    at.text_input[0].input("Ada")
    at.selectbox[0].set_value("25–34")
    at.selectbox[3].set_value("Technology and software")
    at.multiselect[0].set_value(["Artificial intelligence", "Quantum sensing"])
    at.button[[b.label for b in at.button].index("Save profile")].click().run()
    assert not at.exception and at.success
    from src.ui.state import get_repo
    profile = get_repo().get_profile("local")
    assert profile.display_name == "Ada" and profile.age_range == "25–34"
    assert profile.industry == "Technology and software"
    assert profile.research_interests == ["Artificial intelligence", "Quantum sensing"]
    get_repo().delete_user_data("local")
    assert get_repo().get_profile("local").display_name == ""


def test_display_name_is_escaped_in_the_greeting(monkeypatch):
    """The profile display name is user-editable, so the home greeting must show it as text, never as markup."""
    import src.ui.state as ui
    hostile = ui.CurrentUser("local", None, '<img src=x onerror="alert(1)"> Ada', is_guest=False, is_admin=True,
                             auth_enabled=False, provider="local")
    monkeypatch.setattr(ui, "current_user", lambda: hostile)
    at = app(with_auth=False)
    assert not at.exception
    greeting = next(m.value for m in at.markdown if "dr-greet" in m.value)
    assert "<img" not in greeting and "&lt;img" in greeting


def test_damru_name_and_expansion_next_to_logo():
    from src.ui.brand import BRAND_CSS, BRAND_EXPANSION, BRAND_NAME, signin_brand_html
    assert BRAND_NAME == "DAMRU" and BRAND_EXPANSION == "Deep Agent for Multimodal Research and Understanding"
    assert f'title="{BRAND_EXPANSION}">{BRAND_NAME}</abbr>' in signin_brand_html("Welcome back", "x")  # hover text
    assert f'content: "{BRAND_NAME}"' in BRAND_CSS and f'content: "{BRAND_EXPANSION}"' in BRAND_CSS  # sidebar logo
    at = app(with_auth=False)
    assert any(BRAND_EXPANSION in h.proto.body for h in at.get("html"))  # the brand CSS is injected on every page


@pytest.mark.parametrize("workspace", [False, True])
def test_meaningless_question_is_rejected_in_both_composers(workspace):
    at = app(with_auth=False)
    if workspace:
        at.switch_page("app_pages/1_Research_Workspace.py").run()
        if at.segmented_control:
            at.segmented_control(key="q_mode").set_value("live").run()
        at.text_area(key="q_text").set_value("test test test").run()
        at.button(key="start_research").click().run()
    else:
        at.text_area(key="home_q").set_value("test test test").run()
        at.button(key="home_start").click().run()
    assert not at.exception
    alerts = [a.value for a in [*at.error, *at.warning]]
    assert at.error and any("research topic" in alert for alert in alerts)
    from src.ui.state import get_repo
    assert not get_repo().list_runs("local")


@pytest.mark.parametrize("workspace", [False, True])
def test_broad_question_shows_advice_in_both_composers(workspace):
    at = app(with_auth=False)
    if workspace:
        at.switch_page("app_pages/1_Research_Workspace.py").run()
        if at.segmented_control:
            at.segmented_control(key="q_mode").set_value("live").run()
    at.text_area(key="q_text" if workspace else "home_q").set_value("Tell me everything about AI").run()
    assert not at.exception and not at.error
    assert any("may be too broad" in info.value for info in at.info)
    assert any("forecasts are uncertain" in caption.value for caption in at.caption)


@pytest.mark.parametrize("workspace", [False, True])
def test_gibberish_question_is_rejected_in_both_composers(workspace):
    at = app(with_auth=False)
    if workspace:
        at.switch_page("app_pages/1_Research_Workspace.py").run()
        if at.segmented_control:
            at.segmented_control(key="q_mode").set_value("live").run()
        at.text_area(key="q_text").set_value("asdf qwer zxcv").run()
        at.button(key="start_research").click().run()
    else:
        at.text_area(key="home_q").set_value("asdf qwer zxcv").run()
        at.button(key="home_start").click().run()
    assert not at.exception
    alerts = [a.value for a in [*at.error, *at.warning]]
    assert at.error and any("random typing" in alert for alert in alerts)
    from src.ui.state import get_repo
    assert not get_repo().list_runs("local")


@pytest.mark.parametrize("workspace", [False, True])
def test_partly_gibberish_question_warns_and_does_not_start(workspace):
    at = app(with_auth=False)
    if workspace:
        at.switch_page("app_pages/1_Research_Workspace.py").run()
        if at.segmented_control:
            at.segmented_control(key="q_mode").set_value("live").run()
    at.text_area(key="q_text" if workspace else "home_q").set_value("How does asdf affect stock markets").run()
    assert not at.exception and not at.error
    assert any("enter your question again" in warning.value for warning in at.warning)  # live alert while typing
    at.button(key="start_research" if workspace else "home_start").click().run()
    assert not at.exception and at.error
    from src.ui.state import get_repo
    assert not get_repo().list_runs("local")


def test_failed_run_can_be_rerun_from_the_library():
    from src.models.research import DEMO_QUESTIONS, ResearchRequest
    from src.models.run import INTERRUPTED_ERROR
    from src.ui.state import get_repo
    at = app(with_auth=False)
    repo = get_repo()
    repo.create_run("run_interrupted", ResearchRequest(question=DEMO_QUESTIONS[0]), "local", worker_id="dead-process")
    repo.fail_run("run_interrupted", "local", INTERRUPTED_ERROR)
    at.switch_page("app_pages/7_Research_History.py").run()
    assert not at.exception
    at.button(key="rerun_run_interrupted").click().run()
    assert not at.exception and not at.error
    runs = repo.list_runs("local")
    assert len(runs) == 2 and {r.question for r in runs} == {DEMO_QUESTIONS[0]}
    assert next(r for r in runs if r.id == "run_interrupted").status == "failed"  # kept in the history
