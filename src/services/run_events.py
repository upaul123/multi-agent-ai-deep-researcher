"""Live progress for a research run: derived agent statuses and a buffered, durable event sink.

The sink is what makes a run observable from outside the thread that executes it. Every event gets a sequence
number, is buffered, and is written to the database in small batches together with a progress snapshot and a
heartbeat. A background heartbeat keeps long, silent steps (an LLM call, a slow fetch) from looking dead. Any page,
in any session, can then read progress with `Repository.get_progress` instead of holding the run in memory.

Persistence problems never fail the research itself: they are logged, and the final save writes the full log.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from src.models.evidence import AgentEvent
from src.orchestration.state import AGENT_ORDER

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Per-agent status derived from the event stream (shared by the worker and the progress page)."""

    def __init__(self):
        self.statuses: dict[str, str] = {}
        self.messages: dict[str, str] = {}
        self.sources = 0

    def apply(self, event: AgentEvent) -> None:
        self.sources = max(self.sources, int(event.metrics.get("total_sources", 0) or 0))
        if event.agent not in AGENT_ORDER:
            return
        if event.status == "started":
            self.statuses[event.agent] = "running"
        elif event.status in ("completed", "decision"):
            self.statuses[event.agent] = "completed"
        elif event.status == "failed":
            self.statuses[event.agent] = "failed"
        if event.status != "started":
            self.messages[event.agent] = event.message

    @property
    def current(self) -> str | None:
        return next((a for a in AGENT_ORDER if self.statuses.get(a) == "running"), None)

    @property
    def done(self) -> int:
        return sum(self.statuses.get(a) == "completed" for a in AGENT_ORDER)

    def snapshot(self) -> dict:
        return {"statuses": dict(self.statuses), "messages": dict(self.messages), "sources": self.sources,
                "current": self.current, "done": self.done, "total": len(AGENT_ORDER)}

    @classmethod
    def from_snapshot(cls, data: dict) -> ProgressTracker:
        tracker = cls()
        tracker.statuses = dict(data.get("statuses", {}))
        tracker.messages = dict(data.get("messages", {}))
        tracker.sources = int(data.get("sources", 0) or 0)
        return tracker


class EventSink:
    """Numbers, buffers and persists a run's events, progress and heartbeat.

    Use as a context manager around the run: entering starts the heartbeat, leaving flushes everything.
    Each write also reads the run's stop flag, so a stop requested from any session reaches `on_cancel` within one
    flush window (or one heartbeat when the run is quiet).
    `events` is the canonical, ordered log; position i is sequence number i in the database.
    """

    def __init__(self, repo, run_id: str, owner_id: str, *, worker_id: str | None = None,
                 flush_seconds: float = 1.0, heartbeat_seconds: float = 15.0, batch_size: int = 20,
                 on_event: Callable[[AgentEvent], None] | None = None, on_cancel: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.repo, self.run_id, self.owner_id, self.worker_id = repo, run_id, owner_id, worker_id
        self.flush_seconds, self.heartbeat_seconds, self.batch_size = flush_seconds, heartbeat_seconds, batch_size
        self.on_event, self.on_cancel, self.clock = on_event, on_cancel, clock
        self.cancel_requested = False  # the owner asked this run to stop (seen on a progress write)
        self.events: list[AgentEvent] = []
        self.tracker = ProgressTracker()
        self.active = True  # False once the database says the run is no longer queued/running
        self._persisted = 0
        self._last_flush = clock()
        self._lock = threading.Lock()  # events and tracker
        self._flush_lock = threading.Lock()  # one database write at a time, in sequence order
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

    @property
    def fully_persisted(self) -> bool:
        with self._lock:
            return self._persisted == len(self.events)

    def emit(self, event: AgentEvent) -> None:
        with self._lock:
            self.events.append(event)
            self.tracker.apply(event)
            due = (len(self.events) - self._persisted >= self.batch_size
                   or self.clock() - self._last_flush >= self.flush_seconds)
        if self.on_event:
            self.on_event(event)
        if due:
            self.flush()

    def flush(self) -> None:
        with self._flush_lock:
            with self._lock:
                start = self._persisted
                pending = self.events[start:]
                snapshot = self.tracker.snapshot()
            try:
                ack = self.repo.update_progress(self.run_id, self.owner_id, snapshot, pending, start, self.worker_id)
            except Exception as exc:  # progress is best effort; the final save writes the complete log
                logger.warning("Progress write failed for a run: %s", type(exc).__name__)
                return
            with self._lock:
                self._last_flush = self.clock()
                if ack:
                    self._persisted = start + len(pending)
                else:
                    self.active = False
                newly_cancelled = getattr(ack, "cancel_requested", False) and not self.cancel_requested
                self.cancel_requested = self.cancel_requested or newly_cancelled
            if newly_cancelled and self.on_cancel:
                self.on_cancel()

    def _beat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            self.flush()

    def start(self) -> EventSink:
        if self._heartbeat is None:
            self._heartbeat = threading.Thread(target=self._beat, name=f"heartbeat-{self.run_id}", daemon=True)
            self._heartbeat.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=5)
            self._heartbeat = None
        self.flush()

    def __enter__(self) -> EventSink:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()
