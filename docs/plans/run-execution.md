# Plan: durable run execution and checkpointed workflow

**Status:** Phases 0–3 implemented and merged into `feature/synthora-ui` (PR #3); Phases 4–5 pending.
The implemented design is summarised in [architecture.md](../architecture.md#8-run-execution); this document records the
problem, decisions and roadmap.

## Problem (before this work)

Research ran in a thread started by the "Agents in action" page, which blocked in a polling loop while the thread
worked. Progress lived only in that page's memory, and results were written once at the end. As a result, a user who
navigated away or reconnected could not reattach; there was no cancellation and no limit on concurrent runs; and
`mark_stale_runs()` failed *every* running row whenever any app process started, killing live runs on other
instances sharing a Turso database. The LangGraph workflow still has no checkpointer, so a run cannot yet resume
after a process restart or pause for human input (Phases 4–5).

## Decision: in-process job runner behind an interface

The target host is Streamlit Community Cloud (single process, no separate worker service). Runs therefore execute
in one bounded, process-wide executor (`RunManager`), and **all progress is persisted** so pages only submit work
and read progress from the database. The `RunManager` interface allows an external worker later without changing
pages or agents.

Run lifecycle:

```
queued → running → completed
   ↓        ↓
cancelled  cancelled (partial report) · failed (incl. interrupted: process, app or computer stopped) → [Re-run] → new run
```

A run interrupted by a crash or shutdown ends as `failed` with a standard message, and **Re-run** submits its stored
request as a new run. Checkpointing and resuming from the interruption point (Phase 4) was dropped as not required.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Baseline tests; verify checkpoint serialisation of Pydantic state | Done |
| 1 | Persist progress incrementally: run heartbeat/progress/cancel columns, buffered event sink, heartbeat-based stale-run reaping | Done |
| 2 | `RunManager`: bounded executor, per-user and global limits, queued state, cooperative "stop & write report" cancel | Done |
| 3 | DB-driven progress page (`st.fragment(run_every=…)`), reattach from sidebar/library, Stop button | Done |
| 4 | ~~Checkpointer and Resume for interrupted runs~~: replaced by marking interrupted runs failed (including on graceful shutdown) and a **Re-run** action | Dropped (2026-10-12) |
| 5 | Optional plan review via `interrupt()`; paused runs release their worker slot | Not planned |

## Phase 0 findings

- Baseline: 153 tests pass. One parametrised test produced a test id longer than Windows allows in
  `PYTEST_CURRENT_TEST`; it now uses short ids.
- `langgraph-checkpoint==4.2.0` `JsonPlusSerializer` round-trips every state channel (request, plan, sources,
  passages, assessments, claims, matrix, report, statuses, usage, search log, gaps, insights, charts) exactly; a
  full demo state is about 150 KB. Deserialising our classes currently logs "unregistered type … will be blocked in
  a future version", so Phase 4 must pass an explicit `allowed_msgpack_modules` list of `(module, class)` pairs for
  `src.models.*` and `src.orchestration.state` rather than relying on the default.

## Phase 1 design (implemented)

- `runs` gains `worker_id`, `heartbeat_at`, `cancel_requested` and `progress` (JSON) columns via additive migration,
  plus an index on `(status, heartbeat_at)`. Heartbeats and the stale cutoff use UTC (`utc_now`), so hosts in
  different timezones sharing one database agree on staleness (fixed after review; covered by a test).
- `src/services/run_events.py`: `ProgressTracker` derives per-agent status from events (shared by the worker and
  the page); `EventSink` assigns sequence numbers, buffers events and writes them in batches (every
  `RUN_EVENT_FLUSH_SECONDS` or 20 events) together with progress and a heartbeat. A background heartbeat keeps
  long, silent steps (an LLM call) from looking stale.
- Repository: `update_progress`, `get_progress`, `request_cancel`, `is_cancel_requested`,
  `count_active_runs`, `reap_stale_runs`. `reap_stale_runs` replaces `mark_stale_runs` and only fails runs whose
  heartbeat is older than `RUN_STALE_AFTER_SECONDS`.
- `save_run_results` no longer rewrites events that the sink already persisted.

## Phases 2–3 design (implemented)

- `src/services/jobs.py` `RunManager`, one per process via `st.cache_resource` (`get_run_manager`). Daemon worker
  threads (a stopping process does not wait for research; its runs are reaped). `submit` validates, checks
  `MAX_ACTIVE_RUNS_PER_USER` and `MAX_QUEUED_RUNS` in the database, creates a `queued` row and returns at once.
  Workers claim rows with `Repository.start_run` (`queued → running`), so a run cancelled or reaped while waiting
  never starts. The time budget starts when execution starts, not when queued.
- Cancellation: `RunBudget.cancel()` marks `cancelled` as exhausted; searches and LLM calls stop, `decide_route`
  synthesises, and the run ends `cancelled` with a partial report. In-process cancels act immediately; the stored
  flag reaches other instances through `EventSink`'s next progress write (`ProgressAck.cancel_requested`). Queued runs
  are cancelled outright.
- Maintenance thread: heartbeats this process's queued runs, reaps stale runs, purges guest data.
- Deletion: active runs are not deletable from the library; "Delete my data" stops them first; `save_run_results`
  no longer re-creates a run deleted while it executed.
- UI: the workspace submits and opens the progress page; the progress page polls `get_progress` in a fragment
  (only newer events are fetched), shows queue position and a Stop button (an `on_click` callback, which works on
  both fragment and full reruns), and switches to the audit trail when the run finishes. The sidebar lists
  "In progress" runs; the library and recent cards offer "Follow"; results redirect while a run is active.
- Also fixed: the planner never emitted a `completed` event, so live views showed it working for the whole run.

## Risks and mitigations

- **Turso write volume:** event writes are batched.
- **Process restarts:** heartbeat reaping marks interrupted runs failed; Re-run starts them again from the beginning.
- **Repeated work on re-run:** a re-run repeats the whole investigation; the 24-hour search cache bounds the cost.
