"""Durable run execution, phase 2: the process-wide job runner, its limits and cooperative cancellation."""
import threading
from datetime import timedelta

import pytest

from src.models.research import ResearchMode, ResearchRequest, utc_now
from src.services.jobs import RunLimitExceeded, RunManager, ServiceBusy
from src.services.research_service import run_research
from tests.conftest import QUESTION, TODAY
from tests.test_multiuser import ALICE, BOB

CAROL = "google-sub-carol"


class BlockingRunner:
    """Stands in for run_research: records calls and holds each run until released."""

    def __init__(self):
        self.started = threading.Semaphore(0)
        self.release = threading.Event()
        self.calls = []

    def __call__(self, request, settings, *, repo, run_id, owner_id, budget, **kwargs):
        self.calls.append((run_id, kwargs))
        self.started.release()
        self.release.wait(5)
        repo.save_run_results(run_id, {"request": request, "usage": budget.snapshot()},
                              "cancelled" if budget.cancelled else "completed", owner_id)


@pytest.fixture
def manager_factory(repo):
    managers = []

    def make(runner=None, **kwargs):
        kwargs.setdefault("heartbeat_seconds", 3600)  # maintenance is exercised explicitly where needed
        manager = RunManager(repo, runner=runner or BlockingRunner(), **kwargs)
        managers.append(manager)
        return manager
    yield make
    for manager in managers:
        if isinstance(manager.runner, BlockingRunner):
            manager.runner.release.set()
        manager.shutdown()


def status(repo, run_id, owner=ALICE):
    return repo.get_progress(run_id, owner).status


# ------------------------------------------------------------------ submission and limits
def test_submit_returns_immediately_and_runs_in_background(repo, settings, demo_request, manager_factory):
    runner = BlockingRunner()
    manager = manager_factory(runner)
    run_id = manager.submit(demo_request, ALICE, settings)
    assert runner.started.acquire(timeout=5)
    assert status(repo, run_id) == "running"  # claimed by the worker while the page is already free
    call_id, kwargs = runner.calls[0]
    assert call_id == run_id and kwargs["existing_run"] and kwargs["worker_id"] == manager.worker_id
    runner.release.set()
    assert manager.wait(run_id, 5) and status(repo, run_id) == "completed"
    assert [a["kind"] for a in repo.list_activity(ALICE)] == ["run_started"]


def test_per_user_limit(repo, settings, demo_request, manager_factory):
    manager = manager_factory(max_per_user=1)
    manager.submit(demo_request, ALICE, settings)
    with pytest.raises(RunLimitExceeded, match="already have an investigation"):
        manager.submit(demo_request, ALICE, settings)
    manager.submit(demo_request, BOB, settings)  # other users are unaffected
    assert repo.count_active_runs(ALICE) == 1


def test_global_limit_and_queue(repo, settings, demo_request, manager_factory):
    runner = BlockingRunner()
    manager = manager_factory(runner, max_concurrent=1, max_queued=2)
    first = manager.submit(demo_request, ALICE, settings)
    assert runner.started.acquire(timeout=5)
    second = manager.submit(demo_request, BOB, settings)
    assert status(repo, second, BOB) == "queued" and manager.queue_position(second) == 1
    assert manager.queue_position(first) is None  # running, not waiting
    with pytest.raises(ServiceBusy):
        manager.submit(demo_request, CAROL, settings)
    runner.release.set()
    assert manager.wait(first, 5) and manager.wait(second, 5)
    assert status(repo, second, BOB) == "completed"


def test_invalid_requests_are_rejected_before_queueing(repo, settings, manager_factory):
    manager = manager_factory()
    with pytest.raises(PermissionError):
        manager.submit(ResearchRequest(question=QUESTION, mode=ResearchMode.LIVE), "guest:abc", settings)
    with pytest.raises(ValueError, match="preselected demo questions"):
        manager.submit(ResearchRequest(question="Something else entirely?", mode=ResearchMode.DEMO), ALICE, settings)
    assert repo.count_active_runs() == 0


def test_runner_crash_marks_run_failed(repo, settings, demo_request, manager_factory):
    def broken(*args, **kwargs):
        raise RuntimeError("provider set-up exploded")
    manager = manager_factory(broken)
    run_id = manager.submit(demo_request, ALICE, settings)
    assert manager.wait(run_id, 5)
    progress = repo.get_progress(run_id, ALICE)
    assert progress.status == "failed" and "provider set-up exploded" in progress.errors[0]


# ------------------------------------------------------------------ cancellation
def test_cancel_queued_run_never_starts(repo, settings, demo_request, manager_factory):
    runner = BlockingRunner()
    manager = manager_factory(runner, max_concurrent=1)
    manager.submit(demo_request, BOB, settings)
    assert runner.started.acquire(timeout=5)
    waiting = manager.submit(demo_request, ALICE, settings)
    assert manager.cancel(waiting, ALICE)
    assert status(repo, waiting) == "cancelled" and manager.queue_position(waiting) is None
    runner.release.set()
    assert manager.wait(waiting, 5) and len(runner.calls) == 1  # the cancelled run was never executed


def test_cancel_running_run_stops_budget(repo, settings, demo_request, manager_factory):
    runner = BlockingRunner()
    manager = manager_factory(runner)
    run_id = manager.submit(demo_request, ALICE, settings)
    assert runner.started.acquire(timeout=5)
    assert not manager.cancel(run_id, BOB)  # only the owner can stop it
    assert manager.cancel(run_id, ALICE)
    runner.release.set()
    assert manager.wait(run_id, 5) and status(repo, run_id) == "cancelled"
    assert not manager.cancel(run_id, ALICE)  # no longer active


def test_cancel_real_run_produces_partial_report(repo, settings, demo_request, manager_factory):
    """A stop request arriving when retrieval starts ends the run as cancelled, with a report from what was gathered."""
    manager = manager_factory(runner=None)
    holder = {}

    def runner(request, settings_, **kwargs):
        def stop_at_retrieval(event):
            if event.agent == "retriever" and event.status == "started":
                manager.cancel(kwargs["run_id"], kwargs["owner_id"])
        holder["state"] = run_research(request, settings_, today=TODAY, on_event=stop_at_retrieval, **kwargs)
    manager.runner = runner
    run_id = manager.submit(demo_request, ALICE, settings)
    assert manager.wait(run_id, 60)
    state = holder["state"]
    assert state["report"] is not None and state["sources"] == []  # no searches after the stop
    assert "cancelled" in state["usage"].exhausted and state["stop_reason"].startswith("Stopped by user")
    bundle = repo.load_run(run_id, ALICE)
    assert bundle.summary.status == "cancelled" and bundle.report is not None
    assert bundle.stop_reason.startswith("Stopped by user")
    assert "run_cancelled" in [a["kind"] for a in repo.list_activity(ALICE)]


def test_stop_flag_from_another_instance_reaches_worker(repo, settings, demo_request):
    """Without the in-process shortcut, the flag travels through the sink's next progress write."""
    from dataclasses import replace
    settings = replace(settings, run_event_flush_seconds=0)
    repo.create_run("run_remote", demo_request, ALICE)

    def stop_from_elsewhere(event):
        if event.agent == "retriever" and event.status == "started":
            repo.request_cancel("run_remote", ALICE)  # e.g. the Stop button on another app instance
    state = run_research(demo_request, settings, repo=repo, today=TODAY, owner_id=ALICE, run_id="run_remote",
                         existing_run=True, on_event=stop_from_elsewhere)
    assert state["sources"] == [] and repo.load_run("run_remote", ALICE).summary.status == "cancelled"


# ------------------------------------------------------------------ deletion while active
def test_active_runs_are_not_deleted_by_default(repo, demo_request):
    repo.create_run("run_busy", demo_request, ALICE)
    assert not repo.delete_run("run_busy", ALICE)
    assert repo.delete_run("run_busy", ALICE, allow_active=True)


def test_run_deleted_while_executing_is_not_resurrected(repo, settings, demo_request, manager_factory):
    runner = BlockingRunner()
    manager = manager_factory(runner)
    run_id = manager.submit(demo_request, ALICE, settings)
    assert runner.started.acquire(timeout=5)
    repo.delete_user_data(ALICE)
    runner.release.set()
    assert manager.wait(run_id, 5)
    assert repo.load_run(run_id, ALICE) is None and repo.list_runs(ALICE) == []


# ------------------------------------------------------------------ maintenance
def test_maintenance_heartbeats_queue_and_reaps_dead_runs(repo, settings, demo_request, manager_factory):
    runner = BlockingRunner()
    manager = manager_factory(runner, max_concurrent=1, heartbeat_seconds=0.05, housekeeping_seconds=0,
                              stale_after_seconds=120)
    manager.submit(demo_request, BOB, settings)
    assert runner.started.acquire(timeout=5)
    waiting = manager.submit(demo_request, ALICE, settings)
    repo.create_run("run_orphan", demo_request, CAROL, worker_id="crashed-instance")
    with repo.backend.session() as db:
        db.execute("UPDATE runs SET heartbeat_at = ? WHERE id IN (?, ?)",
                   ((utc_now() - timedelta(seconds=600)).isoformat(), waiting, "run_orphan"))
    for _ in range(100):  # within a few maintenance ticks
        if status(repo, "run_orphan", CAROL) == "failed" and repo.reap_stale_runs(120) == []:
            break
        threading.Event().wait(0.05)
    assert status(repo, "run_orphan", CAROL) == "failed"
    assert status(repo, waiting) == "queued"  # our own waiting run was kept alive by heartbeats


def test_meaningless_question_is_rejected_before_a_run_is_created(repo, settings, manager_factory):
    from src.models.research import INVALID_QUESTION, ResearchRequest
    from src.services.question_check import InvalidQuestion

    def reject(request, _settings):
        raise InvalidQuestion(f"{INVALID_QUESTION} It has no coherent topic.")
    manager = manager_factory(question_check=reject)
    request = ResearchRequest(question="Why does the colour seven taste louder than Tuesday", mode="live")
    with pytest.raises(ValueError, match="seems to be invalid! Please check input"):
        manager.submit(request, ALICE, settings)
    assert not repo.list_runs(ALICE)


# ------------------------------------------------------------------ interrupted runs and re-runs
def test_shutdown_interrupts_a_real_run_as_failed_not_stopped(repo, settings, demo_request):
    """An app shutdown mid-run is a failure the user can re-run, not a stop by the user."""
    from src.models.run import INTERRUPTED_ERROR
    holder, started = {}, threading.Event()
    manager = RunManager(repo, runner=None, heartbeat_seconds=3600)

    def runner(request, settings_, **kwargs):
        def shut_down_at_retrieval(event):
            if event.agent == "retriever" and event.status == "started":
                started.set()
                threading.Thread(target=manager.shutdown, kwargs={"timeout": 0}).start()
                threading.Event().wait(0.3)  # the shutdown interrupts the budget while this run is in flight
        holder["state"] = run_research(request, settings_, today=TODAY, on_event=shut_down_at_retrieval, **kwargs)
    manager.runner = runner
    run_id = manager.submit(demo_request, ALICE, settings)
    assert started.wait(60) and manager.wait(run_id, 60)
    bundle = repo.load_run(run_id, ALICE)
    assert bundle.summary.status == "failed" and INTERRUPTED_ERROR in bundle.errors
    assert not bundle.stop_reason.startswith("Stopped by user")


def test_shutdown_fails_runs_still_waiting_in_the_queue(repo, settings, demo_request, manager_factory):
    from src.models.run import INTERRUPTED_ERROR
    runner = BlockingRunner()
    manager = manager_factory(runner, max_concurrent=1)
    manager.submit(demo_request, BOB, settings)
    assert runner.started.acquire(timeout=5)
    waiting = manager.submit(demo_request, ALICE, settings)
    runner.release.set()
    manager.shutdown(timeout=5)
    progress = repo.get_progress(waiting, ALICE)
    assert progress.status == "failed" and progress.errors == [INTERRUPTED_ERROR]
    assert manager.wait(waiting, 1) and len(runner.calls) == 1  # never executed


def test_crashed_runs_are_reaped_with_the_interrupted_message(repo, demo_request):
    from src.models.run import INTERRUPTED_ERROR
    repo.create_run("run_crashed", demo_request, ALICE, worker_id="dead-process")
    with repo.backend.session() as db:
        db.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ?",
                   ((utc_now() - timedelta(seconds=600)).isoformat(), "run_crashed"))
    assert repo.reap_stale_runs(120) == ["run_crashed"]
    progress = repo.get_progress("run_crashed", ALICE)
    assert progress.status == "failed" and progress.errors == [INTERRUPTED_ERROR]


def test_rerun_starts_a_new_run_with_the_same_request(repo, settings, demo_request, manager_factory):
    def broken(*args, **kwargs):
        raise RuntimeError("the computer went to sleep")
    manager = manager_factory(broken)
    failed = manager.submit(demo_request, ALICE, settings)
    assert manager.wait(failed, 5) and status(repo, failed) == "failed"
    runner = BlockingRunner()
    manager.runner = runner
    with pytest.raises(ValueError, match="no longer exists"):
        manager.rerun(failed, BOB, settings)  # only the owner can re-run it
    again = manager.rerun(failed, ALICE, settings)
    assert again != failed and runner.started.acquire(timeout=5)
    assert repo.load_run(again, ALICE).request.question == demo_request.question
    assert status(repo, failed) == "failed"  # the failed run stays in the history
    assert {"kind": "run_rerun", "run_id": again} .items() <= next(
        a for a in repo.list_activity(ALICE) if a["kind"] == "run_rerun").items()
    runner.release.set()
    assert manager.wait(again, 5)
    with pytest.raises(ValueError, match="Only failed"):
        manager.rerun(again, ALICE, settings)
