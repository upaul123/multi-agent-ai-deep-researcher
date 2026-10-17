"""Process-wide research job runner: a bounded worker pool with per-user and global limits.

Pages never execute research themselves. They `submit` a request, which creates a `queued` run row and returns its
id at once, then follow the run through `Repository.get_progress`. That makes a run independent of the browser
session that started it: closing the tab, refreshing or opening the app on another device reattaches to the same run.

- Workers are daemon threads, so a process that stops does not wait for research to finish. Its runs stop sending
  heartbeats and are reaped as failed by the next maintenance pass (here or on another instance).
- Limits are counted in the database, so they hold across app instances sharing one database (best effort: two
  instances admitting at the same instant can briefly exceed a limit by one).
- `cancel` stops a queued run immediately and asks a running one to finish early with a partial report. The flag is
  stored, so a stop requested on another instance reaches the worker through its next progress write.
- A maintenance thread heartbeats this process's queued runs, reaps runs whose worker died, and purges old guest data.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from src.config import Settings
from src.models.research import ResearchRequest, new_id
from src.models.run import INTERRUPTED_ERROR
from src.orchestration.budget import RunBudget
from src.search.registry import ProviderSettings
from src.services.question_check import assess_question
from src.services.research_service import log_run_started, run_research, validate_request

logger = logging.getLogger(__name__)


class RunRejected(RuntimeError):
    """A request that is valid but cannot be accepted now; the message is safe to show to the user."""


class RunLimitExceeded(RunRejected):
    pass


class ServiceBusy(RunRejected):
    pass


@dataclass
class _Job:
    run_id: str
    owner_id: str
    request: ResearchRequest
    settings: Settings
    provider_settings: ProviderSettings
    budget: RunBudget | None = None  # created when execution starts, so queue time does not use the time budget
    done: threading.Event = field(default_factory=threading.Event)


class RunManager:
    def __init__(self, repo, *, max_concurrent: int = 2, max_per_user: int = 1, max_queued: int = 10,
                 heartbeat_seconds: float = 15.0, stale_after_seconds: float = 120.0,
                 housekeeping_seconds: float = 60.0, worker_id: str | None = None,
                 runner: Callable[..., object] = run_research,
                 question_check: Callable[[ResearchRequest, Settings], None] = assess_question):
        self.repo, self.runner, self.question_check = repo, runner, question_check
        self.max_per_user, self.max_queued = max_per_user, max_queued
        self.heartbeat_seconds, self.stale_after_seconds = heartbeat_seconds, stale_after_seconds
        self.housekeeping_seconds = housekeeping_seconds
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:10]}"
        self._queue: deque[_Job] = deque()
        self._jobs: dict[str, _Job] = {}  # queued and running in this process
        self._cond = threading.Condition()
        self._admit = threading.Lock()  # limit check + row creation are one step within this process
        self._closed = False
        self._stop = threading.Event()
        self._workers = [threading.Thread(target=self._work, name=f"research-worker-{i}", daemon=True)
                         for i in range(max(1, max_concurrent))]
        self._maintenance = threading.Thread(target=self._maintain, name="research-maintenance", daemon=True)
        for thread in (*self._workers, self._maintenance):
            thread.start()

    @classmethod
    def from_settings(cls, repo, settings: Settings) -> RunManager:
        return cls(repo, max_concurrent=settings.max_concurrent_runs, max_per_user=settings.max_active_runs_per_user,
                   max_queued=settings.max_queued_runs, heartbeat_seconds=settings.run_heartbeat_seconds,
                   stale_after_seconds=settings.run_stale_after_seconds)

    # ------------------------------------------------------------------ public API
    def submit(self, request: ResearchRequest, owner_id: str, settings: Settings,
               provider_settings: ProviderSettings | None = None) -> str:
        """Queue an investigation and return its run id. Raises `RunRejected` when a limit is reached, and
        `InvalidQuestion` (a `ValueError`) when the LLM judges the question meaningless."""
        validate_request(request, owner_id)
        self.question_check(request, settings)  # before admission: a rejected question never creates a run
        with self._admit:
            if self._closed:
                raise ServiceBusy("The research service is restarting. Please try again in a moment.")
            if self.repo.count_active_runs(owner_id) >= self.max_per_user:
                raise RunLimitExceeded("You already have an investigation in progress. Open it from the sidebar, "
                                       "stop it, or wait for it to finish before starting another.")
            if self.repo.count_active_runs() >= self.max_queued:
                raise ServiceBusy("The research service is busy with other investigations. Please try again in a "
                                  "few minutes.")
            run_id = new_id("run")
            self.repo.create_run(run_id, request, owner_id, status="queued", worker_id=self.worker_id)
        log_run_started(self.repo, run_id, request, owner_id)
        job = _Job(run_id, owner_id, request, settings, provider_settings or ProviderSettings())
        with self._cond:
            self._jobs[run_id] = job
            self._queue.append(job)
            self._cond.notify()
        return run_id

    def cancel(self, run_id: str, owner_id: str) -> bool:
        """Stop a run owned by `owner_id`. Returns False if it is not active (or not theirs)."""
        if not self.repo.request_cancel(run_id, owner_id):
            return False
        with self._cond:
            job = self._jobs.get(run_id)
            if job and job.owner_id == owner_id:
                if job.budget is not None:
                    job.budget.cancel()  # this process runs it: stop now rather than at the next progress write
                elif job in self._queue:
                    self._queue.remove(job)
                    self._jobs.pop(run_id, None)
                    job.done.set()
        return True

    def queue_position(self, run_id: str) -> int | None:
        """1-based position among runs waiting in this process, or None if not waiting here."""
        with self._cond:
            for position, job in enumerate(self._queue, start=1):
                if job.run_id == run_id:
                    return position
        return None

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        """Block until a run submitted here has finished. True if finished (or not running here)."""
        with self._cond:
            job = self._jobs.get(run_id)
        return job.done.wait(timeout) if job else True

    def rerun(self, run_id: str, owner_id: str, settings: Settings,
              provider_settings: ProviderSettings | None = None) -> str:
        """Start a new run with the request of `owner_id`'s failed run `run_id`; the failed run stays in history.

        The request goes through `submit` again, so current limits, permissions and question checks apply.
        """
        bundle = self.repo.load_run(run_id, owner_id)
        if bundle is None:
            raise ValueError("This investigation no longer exists.")
        if bundle.summary.status != "failed":
            raise ValueError("Only failed investigations can be re-run.")
        new_run_id = self.submit(bundle.request.model_copy(deep=True), owner_id, settings, provider_settings)
        self.repo.log_activity(owner_id, "run_rerun", new_run_id, {"rerun_of": run_id})
        return new_run_id

    def shutdown(self, timeout: float | None = 5.0) -> None:
        """Stop accepting work and the threads. Runs interrupted by the shutdown end as failed, so they can be re-run."""
        with self._cond:
            self._closed = True
            for job in list(self._jobs.values()):
                if job.budget is not None:
                    job.budget.interrupt()  # the worker writes what it has and records the run as failed
                elif job in self._queue:  # still waiting in this process's queue: it will never start here
                    self.repo.fail_run(job.run_id, job.owner_id, INTERRUPTED_ERROR)
                    self._jobs.pop(job.run_id, None)
                    job.done.set()
            self._queue.clear()
            self._cond.notify_all()
        self._stop.set()
        for thread in (*self._workers, self._maintenance):
            thread.join(timeout)

    # ------------------------------------------------------------------ workers
    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                job = self._queue.popleft()
            self._execute(job)

    def _execute(self, job: _Job) -> None:
        try:
            if not self.repo.start_run(job.run_id, job.owner_id, self.worker_id):
                return  # cancelled, reaped or deleted while it waited
            budget = RunBudget(job.request.budget())
            with self._cond:
                job.budget = budget
            if self.repo.is_cancel_requested(job.run_id):  # stopped between the claim and now
                budget.cancel()
            self.runner(job.request, job.settings, repo=self.repo, provider_settings=job.provider_settings,
                        run_id=job.run_id, owner_id=job.owner_id, worker_id=self.worker_id, existing_run=True,
                        budget=budget)
        except Exception as exc:  # the workflow keeps its own errors; this is a failure around it
            logger.exception("Research job failed outside the workflow")
            try:
                self.repo.fail_run(job.run_id, job.owner_id, f"Research could not run: {type(exc).__name__}: "
                                                             f"{str(exc)[:200]}")
            except Exception:
                logger.exception("Could not record a failed research job")
        finally:
            with self._cond:
                self._jobs.pop(job.run_id, None)
            job.done.set()

    # ------------------------------------------------------------------ maintenance
    def _maintain(self) -> None:
        last_housekeeping = float("-inf")
        while not self._stop.wait(self.heartbeat_seconds):
            with self._cond:
                waiting = [job.run_id for job in self._queue]
            try:
                self.repo.heartbeat_runs(waiting)
                if time.monotonic() - last_housekeeping >= self.housekeeping_seconds:
                    last_housekeeping = time.monotonic()
                    reaped = self.repo.reap_stale_runs(self.stale_after_seconds)
                    if reaped:
                        logger.warning("Marked %d abandoned research run(s) as failed", len(reaped))
                    self.repo.purge_guest_data()
            except Exception as exc:  # the database may be briefly unavailable; try again next tick
                logger.warning("Research maintenance failed: %s", type(exc).__name__)
