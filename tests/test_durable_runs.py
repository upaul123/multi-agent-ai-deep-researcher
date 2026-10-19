"""Durable run execution, phase 1: persisted progress, heartbeats, cancellation flags and stale-run reaping."""
import threading
from dataclasses import replace
from datetime import timedelta

import pytest

from src.models.evidence import AgentEvent
from src.models.research import utc_now
from src.services.research_service import run_research
from src.services.run_events import EventSink, ProgressTracker
from tests.conftest import TODAY
from tests.test_multiuser import ALICE, BOB, any_repo  # noqa: F401  (fixture: SQLite and libSQL backends)


def event(agent="planner", status="started", message="m", **metrics):
    return AgentEvent(agent=agent, status=status, message=message, metrics=metrics)


def backdate_heartbeat(repo, run_id, seconds):
    with repo.backend.session() as db:
        db.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ?",
                   ((utc_now() - timedelta(seconds=seconds)).isoformat(), run_id))


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ progress tracker
def test_progress_tracker_derives_agent_states():
    tracker = ProgressTracker()
    for e in (event("planner", "started"), event("planner", "completed", "plan ready"),
              event("retriever", "started"), event("retriever", "search", "q", total_sources=7),
              event("orchestrator", "info", "ignored for statuses")):
        tracker.apply(e)
    snap = tracker.snapshot()
    assert snap["statuses"] == {"planner": "completed", "retriever": "running"}
    assert snap["current"] == "retriever" and snap["done"] == 1 and snap["sources"] == 7
    assert snap["messages"]["planner"] == "plan ready"
    assert ProgressTracker.from_snapshot(snap).snapshot() == snap


# ------------------------------------------------------------------ repository progress API
def test_events_and_progress_are_readable_while_running(any_repo, demo_request):
    any_repo.create_run("run_live", demo_request, ALICE, worker_id="w1")
    assert any_repo.update_progress("run_live", ALICE, {"done": 1}, [event(), event(status="completed")], 0)
    assert any_repo.update_progress("run_live", ALICE, {"done": 2}, [event("retriever")], 2)
    progress = any_repo.get_progress("run_live", ALICE)
    assert progress.active and progress.progress == {"done": 2} and progress.last_seq == 2
    assert [e.agent for e in progress.events] == ["planner", "planner", "retriever"]
    newer = any_repo.get_progress("run_live", ALICE, after_seq=1)
    assert [e.agent for e in newer.events] == ["retriever"] and newer.last_seq == 2
    assert any_repo.get_progress("run_live", ALICE, after_seq=2).events == []


def test_progress_is_owner_scoped(any_repo, demo_request):
    any_repo.create_run("run_a", demo_request, ALICE)
    assert any_repo.get_progress("run_a", BOB) is None
    assert not any_repo.update_progress("run_a", BOB, {"x": 1}, [event()], 0)
    assert not any_repo.request_cancel("run_a", BOB)
    assert any_repo.get_progress("run_a", ALICE).events == []  # Bob's write was rejected


def test_cancel_flag_and_active_counts(any_repo, demo_request):
    any_repo.create_run("run_a", demo_request, ALICE)
    any_repo.create_run("run_q", demo_request, ALICE, status="queued")
    any_repo.create_run("run_b", demo_request, BOB)
    assert any_repo.count_active_runs(ALICE) == 2 and any_repo.count_active_runs() == 3
    assert not any_repo.is_cancel_requested("run_a")
    assert any_repo.request_cancel("run_a", ALICE) and any_repo.is_cancel_requested("run_a")
    assert any_repo.get_progress("run_a", ALICE).cancel_requested


def test_reaper_only_fails_runs_without_recent_heartbeat(any_repo, demo_request):
    any_repo.create_run("run_dead", demo_request, ALICE, worker_id="crashed")
    any_repo.create_run("run_alive", demo_request, BOB, worker_id="other-instance")
    backdate_heartbeat(any_repo, "run_dead", 600)
    assert any_repo.reap_stale_runs(stale_after_seconds=120) == ["run_dead"]
    assert any_repo.get_progress("run_dead", ALICE).status == "failed"
    assert any_repo.get_progress("run_alive", BOB).status == "running"  # another instance's live run survives
    # a reaped run no longer accepts progress, so its worker learns it should stop
    assert not any_repo.update_progress("run_dead", ALICE, {"x": 1})
    assert not any_repo.request_cancel("run_dead", ALICE)


def test_heartbeat_keeps_a_run_alive(any_repo, demo_request):
    any_repo.create_run("run_slow", demo_request, ALICE)
    backdate_heartbeat(any_repo, "run_slow", 600)
    assert any_repo.update_progress("run_slow", ALICE)  # heartbeat only
    assert any_repo.reap_stale_runs(stale_after_seconds=120) == []


def test_heartbeats_agree_across_hosts_in_different_timezones(any_repo, demo_request, monkeypatch):
    """A laptop in UTC+5:30 and a cloud host in UTC share one database: neither may reap the other's live run."""
    import src.database.repository as repository
    from datetime import datetime, timezone

    def host_clock(offset_hours):
        return lambda: (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).replace(tzinfo=None, microsecond=0)

    monkeypatch.setattr(repository, "now", host_clock(0))  # cloud host (UTC) starts a run
    any_repo.create_run("run_cloud", demo_request, ALICE, worker_id="cloud")
    assert any_repo.update_progress("run_cloud", ALICE, {"step": 1})
    monkeypatch.setattr(repository, "now", host_clock(5.5))  # the laptop (IST) runs maintenance
    assert any_repo.reap_stale_runs(stale_after_seconds=120) == []
    any_repo.create_run("run_laptop", demo_request, BOB, worker_id="laptop")
    monkeypatch.setattr(repository, "now", host_clock(0))  # and the cloud host does too
    assert any_repo.reap_stale_runs(stale_after_seconds=120) == []
    backdate_heartbeat(any_repo, "run_laptop", 600)  # once the laptop really stops, the cloud reaps it
    assert any_repo.reap_stale_runs(stale_after_seconds=120) == ["run_laptop"]


def test_migration_adds_execution_columns(tmp_path):
    import sqlite3
    from src.database.repository import Repository
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:  # a runs table from before durable execution
        conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL, "
                     "mode TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT, parent_run_id TEXT, "
                     "request TEXT NOT NULL, plan TEXT, usage TEXT, statuses TEXT, stop_reason TEXT, summary TEXT, "
                     "warnings TEXT NOT NULL DEFAULT '[]', errors TEXT NOT NULL DEFAULT '[]', "
                     "provider_notes TEXT NOT NULL DEFAULT '[]', counts TEXT NOT NULL DEFAULT '{}')")
        conn.execute("INSERT INTO runs (id, question, status, mode, created_at, request) "
                     "VALUES ('run_old', 'q', 'running', 'demo', '2026-01-01T00:00:00', '{}')")
    repo = Repository(path)
    assert repo.reap_stale_runs(stale_after_seconds=120) == ["run_old"]  # legacy rows have no heartbeat
    progress = repo.get_progress("run_old", "local")
    assert progress.status == "failed" and progress.progress == {} and not progress.cancel_requested


# ------------------------------------------------------------------ event sink
def test_sink_batches_by_time_and_size(any_repo, demo_request):
    any_repo.create_run("run_s", demo_request, ALICE)
    clock = FakeClock()
    seen = []
    sink = EventSink(any_repo, "run_s", ALICE, flush_seconds=1.0, batch_size=3, on_event=seen.append, clock=clock)
    sink.emit(event())
    sink.emit(event(status="completed"))
    assert any_repo.get_progress("run_s", ALICE).events == []  # buffered
    sink.emit(event("retriever"))  # batch size reached
    assert len(any_repo.get_progress("run_s", ALICE).events) == 3 and sink.fully_persisted
    sink.emit(event("retriever", "completed", total_sources=4))
    assert len(any_repo.get_progress("run_s", ALICE).events) == 3
    clock.t = 1.5
    sink.emit(event("source_evaluator"))  # flush window elapsed
    progress = any_repo.get_progress("run_s", ALICE)
    assert [e.agent for e in progress.events][-2:] == ["retriever", "source_evaluator"]
    assert progress.progress["sources"] == 4 and progress.progress["current"] == "source_evaluator"
    assert len(seen) == 5  # the in-process callback still sees every event immediately


def test_sink_heartbeat_runs_in_background(any_repo, demo_request):
    any_repo.create_run("run_h", demo_request, ALICE)
    backdate_heartbeat(any_repo, "run_h", 600)
    beat = threading.Event()
    original = any_repo.update_progress

    def spy(*args, **kwargs):
        result = original(*args, **kwargs)
        beat.set()
        return result
    any_repo.update_progress = spy
    with EventSink(any_repo, "run_h", ALICE, heartbeat_seconds=0.05):
        assert beat.wait(2)
    assert any_repo.reap_stale_runs(stale_after_seconds=120) == []


def test_sink_stops_persisting_once_run_is_reaped(any_repo, demo_request):
    any_repo.create_run("run_r", demo_request, ALICE)
    sink = EventSink(any_repo, "run_r", ALICE, batch_size=1)
    sink.emit(event())
    backdate_heartbeat(any_repo, "run_r", 600)
    any_repo.reap_stale_runs(stale_after_seconds=120)
    sink.emit(event(status="completed"))
    assert not sink.active and not sink.fully_persisted


def test_sink_survives_database_errors(demo_request):
    class BrokenRepo:
        def update_progress(self, *args, **kwargs):
            raise RuntimeError("database unavailable")
    sink = EventSink(BrokenRepo(), "run_x", ALICE, batch_size=1)
    sink.emit(event())  # logged, not raised
    assert sink.events and not sink.fully_persisted


# ------------------------------------------------------------------ end to end through the research service
def test_run_persists_progress_incrementally(any_repo, settings, demo_request):
    snapshots = []

    def on_event(e):  # read progress from the database while the run is still executing
        if e.agent == "report_builder" and e.status == "started":
            snapshots.append(any_repo.get_progress(run_id, ALICE))
    run_id = "run_incremental"
    settings = replace(settings, run_event_flush_seconds=0)  # write every event, so the mid-run read is exact
    state = run_research(demo_request, settings, repo=any_repo, today=TODAY, owner_id=ALICE, run_id=run_id,
                         on_event=on_event, worker_id="worker-1")
    [mid] = snapshots
    assert mid.status == "running" and mid.events and mid.progress["done"] >= 6
    final = any_repo.get_progress(run_id, ALICE)
    assert final.status == "completed" and not final.active
    bundle = any_repo.load_run(run_id, ALICE)
    assert [e.message for e in bundle.events] == [e.message for e in state["events"]]  # no duplicates or gaps
    assert bundle.events[-1].agent == "orchestrator"


@pytest.mark.parametrize("persisted", [True, False])
def test_final_save_writes_full_log_when_sink_fell_behind(any_repo, settings, demo_request, monkeypatch, persisted):
    if not persisted:  # every progress write fails: the final save must still store the complete log
        monkeypatch.setattr(any_repo, "update_progress", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    state = run_research(demo_request, settings, repo=any_repo, today=TODAY, owner_id=ALICE)
    bundle = any_repo.load_run(state["run_id"], ALICE)
    assert len(bundle.events) == len(state["events"]) and bundle.summary.status == "completed"


def test_progress_event_batch_retry_is_idempotent(any_repo, demo_request):
    any_repo.create_run("run_retry", demo_request, ALICE)
    batch = [event(), event(status="completed")]
    for _ in range(2):
        assert any_repo.update_progress("run_retry", ALICE, {"done": 1}, batch, 0)
    assert any_repo.update_progress("run_retry", ALICE, {"done": 2}, [event("retriever")], 2)
    progress = any_repo.get_progress("run_retry", ALICE)
    assert progress.progress == {"done": 2} and progress.last_seq == 2
    assert [e.agent for e in progress.events] == ["planner", "planner", "retriever"]
    assert [e.status for e in progress.events] == ["started", "completed", "started"]
