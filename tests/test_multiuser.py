"""Multi-user data isolation, guests, activity, migration and the libSQL (Turso) backend."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from src.database.backend import Session
from src.database.repository import LOCAL_OWNER, Repository
from src.models.research import ResearchMode, ResearchRequest
from src.services.research_service import run_research
from tests.conftest import QUESTION, TODAY

ALICE, BOB = "google-sub-alice", "google-sub-bob"


class LocalLibsqlBackend:
    """libSQL engine on a local file: same SQL dialect and driver as Turso, without the network."""

    name = "libsql-local"

    def __init__(self, path):
        import libsql
        self._conn = libsql.connect(str(path), _check_same_thread=False)
        self._lock = threading.RLock()

    @contextmanager
    def session(self):
        with self._lock:
            try:
                yield Session(self._conn)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def describe(self):
        return "local libSQL"


@pytest.fixture(params=["sqlite", "libsql"])
def any_repo(request, tmp_path):
    if request.param == "libsql":
        return Repository(tmp_path / "x.db", backend=LocalLibsqlBackend(tmp_path / "libsql.db"))
    return Repository(tmp_path / "x.db")


@pytest.fixture
def alice_run(any_repo, settings):
    state = run_research(ResearchRequest(question=QUESTION), settings, repo=any_repo, today=TODAY, owner_id=ALICE)
    return any_repo, state["run_id"]


# ------------------------------------------------------------------ isolation
def test_runs_are_private_per_user(alice_run):
    repo, run_id = alice_run
    assert [r.id for r in repo.list_runs(ALICE)] == [run_id]
    assert repo.list_runs(BOB) == [] and repo.list_runs(LOCAL_OWNER) == []
    assert repo.load_run(run_id, ALICE) is not None
    assert repo.load_run(run_id, BOB) is None  # guessing someone else's run id reveals nothing
    assert repo.delete_run(run_id, BOB) is False and repo.load_run(run_id, ALICE) is not None
    with pytest.raises(PermissionError):
        repo.save_qa(run_id, "q", {"answer": "a"}, BOB)
    repo.save_qa(run_id, "What changed?", {"answer": "a"}, ALICE)
    assert repo.list_qa(run_id, BOB) == [] and len(repo.list_qa(run_id, ALICE)) == 1
    assert repo.delete_run(run_id, ALICE) is True and repo.list_runs(ALICE) == []


def test_documents_are_private_per_user(any_repo):
    doc_a, created_a = any_repo.save_document("a.txt", "A", "text", b"same bytes", [("paragraph 1", "x")], [], ALICE)
    doc_b, created_b = any_repo.save_document("b.txt", "B", "text", b"same bytes", [("paragraph 1", "x")], [], BOB)
    assert created_a and created_b and doc_a != doc_b  # the same file may be uploaded by different users
    assert [d["id"] for d in any_repo.list_documents(ALICE)] == [doc_a]
    assert any_repo.get_documents([doc_a], BOB) == []  # cannot read another user's document
    any_repo.delete_document(doc_a, BOB)
    assert any_repo.list_documents(ALICE)  # not deleted by someone else
    any_repo.delete_document(doc_a, ALICE)
    assert any_repo.list_documents(ALICE) == []


def test_research_uses_only_the_owners_documents(any_repo, settings):
    doc_b, _ = any_repo.save_document("bob.md", "Bob's notes", "markdown", b"bob", [("section 'x'", "Agentic AI notes " * 20)],
                                      [], BOB)
    state = run_research(ResearchRequest(question=QUESTION, document_ids=[doc_b]), settings, repo=any_repo, today=TODAY,
                         owner_id=ALICE)
    assert not any(s.retrieval_method == "upload" for s in state["sources"])


def test_activity_users_and_settings(alice_run):
    repo, run_id = alice_run
    repo.upsert_user(ALICE, "alice@example.com", "Alice")
    repo.upsert_user(ALICE, "alice@example.com", "Alice A.")  # second login updates, not duplicates
    assert repo.get_user(ALICE)["name"] == "Alice A."
    kinds = [a["kind"] for a in repo.list_activity(ALICE)]
    assert "run_started" in kinds and "run_completed" in kinds and repo.list_activity(BOB) == []
    [stats] = [s for s in repo.user_stats() if s["id"] == ALICE]
    assert stats["runs"] == 1 and stats["documents"] == 0
    repo.set_user_setting(ALICE, "pref_depth", "deep")
    assert repo.get_user_setting(ALICE, "pref_depth") == "deep" and repo.get_user_setting(BOB, "pref_depth") is None


def test_delete_my_data_removes_only_that_user(any_repo, settings):
    for owner in (ALICE, BOB):
        run_research(ResearchRequest(question=QUESTION), settings, repo=any_repo, today=TODAY, owner_id=owner)
        any_repo.save_document(f"{owner}.txt", "d", "text", owner.encode(), [("p", "x")], [], owner)
        any_repo.set_user_setting(owner, "pref_mode", "demo")
    assert any_repo.delete_user_data(ALICE) == 1
    assert any_repo.list_runs(ALICE) == [] and any_repo.list_documents(ALICE) == [] and any_repo.list_activity(ALICE) == []
    assert any_repo.get_user_setting(ALICE, "pref_mode") is None
    assert len(any_repo.list_runs(BOB)) == 1 and any_repo.list_documents(BOB) and any_repo.list_activity(BOB)


# ------------------------------------------------------------------ guests
def test_guests_cannot_run_live_research(repo, settings):
    with pytest.raises(PermissionError, match="requires signing in"):
        run_research(ResearchRequest(question=QUESTION, mode=ResearchMode.LIVE), settings, repo=repo, today=TODAY,
                     owner_id="guest:abc")
    state = run_research(ResearchRequest(question=QUESTION), settings, repo=repo, today=TODAY, owner_id="guest:abc")
    assert state["report"] is not None and repo.list_runs("guest:abc")


def test_demo_mode_only_runs_preselected_questions(repo, settings):
    from src.models.research import DEMO_QUESTIONS, is_demo_question
    assert len(DEMO_QUESTIONS) == 3 and all(is_demo_question(q) for q in DEMO_QUESTIONS)
    assert is_demo_question("  " + DEMO_QUESTIONS[0].upper().replace(" ", "   "))  # whitespace / case insensitive
    for owner in ("guest:abc", ALICE, LOCAL_OWNER):  # applies to everyone, signed in or not
        with pytest.raises(ValueError, match="preselected demo questions"):
            run_research(ResearchRequest(question="How will solid-state batteries reshape energy storage?"), settings,
                         repo=repo, today=TODAY, owner_id=owner)
    assert repo.list_runs("guest:abc") == []  # rejected before anything is stored
    for question in DEMO_QUESTIONS:
        state = run_research(ResearchRequest(question=question), settings, repo=repo, today=TODAY, owner_id="guest:abc")
        assert state["report"] is not None and len(state["sources"]) >= 8


def test_guest_data_is_purged_after_24h(repo, settings):
    state = run_research(ResearchRequest(question=QUESTION), settings, repo=repo, today=TODAY, owner_id="guest:old")
    run_research(ResearchRequest(question=QUESTION), settings, repo=repo, today=TODAY, owner_id=ALICE)
    old = (datetime.now() - timedelta(hours=30)).isoformat()
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (old, state["run_id"]))
        conn.execute("UPDATE activity SET created_at = ? WHERE owner_id = 'guest:old'", (old,))
    assert repo.purge_guest_data() == 1
    assert repo.list_runs("guest:old") == [] and repo.list_activity("guest:old") == []
    assert len(repo.list_runs(ALICE)) == 1  # signed-in users' data is never purged
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE run_id = ?", (state["run_id"],)).fetchone()[0] == 0


# ------------------------------------------------------------------ migration & backend
def test_migrates_single_user_database(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:  # schema as it was before multi-user support
        conn.executescript("""
            CREATE TABLE runs (id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL, mode TEXT NOT NULL,
                created_at TEXT NOT NULL, completed_at TEXT, parent_run_id TEXT, request TEXT NOT NULL, plan TEXT,
                usage TEXT, statuses TEXT, stop_reason TEXT, summary TEXT, warnings TEXT NOT NULL DEFAULT '[]',
                errors TEXT NOT NULL DEFAULT '[]', provider_notes TEXT NOT NULL DEFAULT '[]', counts TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL, sections TEXT NOT NULL,
                warnings TEXT NOT NULL DEFAULT '[]', uploaded_at TEXT NOT NULL);
            INSERT INTO runs (id, question, status, mode, created_at, request) VALUES ('run_old', 'Old question here', 'completed',
                'demo', '2026-01-01T00:00:00', '{"question": "Old question here"}');
            INSERT INTO documents VALUES ('doc_old', 'a.txt', 'A', 'text', 'abc', 3, '[]', '[]', '2026-01-01T00:00:00');""")
    repo = Repository(path)
    assert [r.id for r in repo.list_runs(LOCAL_OWNER)] == ["run_old"]  # existing data now belongs to "local"
    assert [d["id"] for d in repo.list_documents(LOCAL_OWNER)] == ["doc_old"]
    Repository(path)  # migration is idempotent


def test_insert_many_chunks_statements(tmp_path):
    executed = []

    class Recorder:
        def execute(self, sql, params=()):
            executed.append(len(params))

    Session(Recorder()).insert_many("t", ("a", "b", "c"), [(i, i, i) for i in range(1000)])
    assert len(executed) == 4 and all(n <= 900 for n in executed) and sum(executed) == 3000


def test_full_run_round_trips_on_libsql(tmp_path, settings):
    repo = Repository(tmp_path / "x.db", backend=LocalLibsqlBackend(tmp_path / "turso-like.db"))
    state = run_research(ResearchRequest(question=QUESTION), settings, repo=repo, today=TODAY, owner_id=ALICE)
    bundle = repo.load_run(state["run_id"], ALICE)
    assert bundle.report.title == state["report"].title
    assert len(bundle.claims) == len(state["claims"]) and len(bundle.passages) == len(state["passages"])
    assert repo.storage_description == "local libSQL"


def test_rows_lowercase_column_names(tmp_path):
    conn = sqlite3.connect(tmp_path / "c.db")
    conn.execute("CREATE TABLE t (plan TEXT)")
    conn.execute("INSERT INTO t VALUES ('x')")
    assert Session(conn).rows('SELECT plan AS "PLAN" FROM t') == [{"plan": "x"}]  # as Turso returns it


def test_backend_selection(tmp_path, monkeypatch):
    from src.database.backend import SQLiteBackend, make_backend
    assert isinstance(make_backend(tmp_path / "a.db"), SQLiteBackend)
    assert isinstance(make_backend(tmp_path / "a.db", "libsql://x.turso.io", None), SQLiteBackend)  # token missing
    assert isinstance(make_backend(tmp_path / "a.db", None, "test-token"), SQLiteBackend)  # URL missing
    from unittest.mock import Mock
    import src.database.backend as backend
    constructor = Mock()
    monkeypatch.setattr(backend, "TursoBackend", constructor)
    selected = make_backend(tmp_path / "a.db", "libsql://example.turso.io", "test-token")
    assert selected is constructor.return_value
    constructor.assert_called_once_with("libsql://example.turso.io", "test-token",
                                        replica_path=tmp_path / "turso-replica.db")
