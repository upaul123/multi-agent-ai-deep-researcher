"""Repository: research runs, evidence, documents, users, activity, search cache and settings.

All SQL lives here. Data is OWNER-SCOPED: every method that reads or changes a run or document takes the owner id
of the signed-in user and filters by it, so one user can never list, open, delete or export another user's data
(even by guessing a run id). The storage engine is pluggable: local SQLite or Turso (see backend.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.database.backend import make_backend
from src.models.profile import UserProfile
from src.models.evidence import (AgentEvent, AgentStatus, Claim, Contradiction, EvidenceLink, Insight, MatrixRow,
                                 Passage, Report, ResearchGap, Source, SourceAssessment)
from src.models.research import BudgetUsage, ResearchPlan, ResearchRequest, new_id, now, utc_now
from src.models.run import ACTIVE_STATUSES, INTERRUPTED_ERROR, RunBundle, RunProgress, RunSummary
from src.models.visualization import EvidenceChart

logger = logging.getLogger(__name__)
SCHEMA = Path(__file__).with_name("schema.sql")
LOCAL_OWNER = "local"
RUN_CHILD_TABLES = ("sources", "passages", "assessments", "claims", "matrix_rows", "evidence_links", "contradictions",
                    "gaps", "insights", "charts", "reports", "events", "search_log", "qa_messages")


@dataclass(frozen=True)
class ProgressAck:
    """Result of a progress write: truthy while the run is still active."""

    active: bool
    cancel_requested: bool = False

    def __bool__(self) -> bool:
        return self.active


def _dump(model) -> str:
    return model.model_dump_json() if hasattr(model, "model_dump_json") else json.dumps(model, default=str)


class Repository:
    def __init__(self, db_path: Path | str, turso_url: str | None = None, turso_token: str | None = None, backend=None,
                 on_run_deleted: Callable[[str], None] | None = None):
        self.db_path = Path(db_path)
        self.backend = backend or make_backend(self.db_path, turso_url, turso_token)
        self.on_run_deleted = on_run_deleted  # removes a run's vectors in Pinecone; the database is the source of truth
        with self.backend.session() as db:
            db.script(SCHEMA.read_text())
            self._migrate(db)

    @property
    def storage_description(self) -> str:
        return self.backend.describe()

    def _migrate(self, db) -> None:
        """Bring databases created before multi-user support up to date (existing data becomes 'local')."""
        run_cols = {r["name"] for r in db.rows("PRAGMA table_info(runs)")}
        if "owner_id" not in run_cols:
            db.execute(f"ALTER TABLE runs ADD COLUMN owner_id TEXT NOT NULL DEFAULT '{LOCAL_OWNER}'")
        doc_cols = {r["name"] for r in db.rows("PRAGMA table_info(documents)")}
        if "owner_id" not in doc_cols:  # rebuild: the uniqueness rule changes from (sha256) to (owner_id, sha256)
            db.script(f"""
                ALTER TABLE documents RENAME TO documents_old;
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL DEFAULT '{LOCAL_OWNER}', filename TEXT NOT NULL,
                    title TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    sections TEXT NOT NULL, warnings TEXT NOT NULL DEFAULT '[]', uploaded_at TEXT NOT NULL,
                    UNIQUE (owner_id, sha256));
                INSERT INTO documents (id, owner_id, filename, title, kind, sha256, size_bytes, sections, warnings, uploaded_at)
                    SELECT id, '{LOCAL_OWNER}', filename, title, kind, sha256, size_bytes, sections, warnings, uploaded_at
                    FROM documents_old;
                DROP TABLE documents_old;""")
        doc_cols = {r["name"] for r in db.rows("PRAGMA table_info(documents)")}
        if "metadata" not in doc_cols:
            db.execute("ALTER TABLE documents ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        if "visualization_notes" not in run_cols:
            db.execute("ALTER TABLE runs ADD COLUMN visualization_notes TEXT NOT NULL DEFAULT '[]'")
        # Durable execution: which process runs it, when it last proved alive, cancellation and live progress.
        for column, ddl in (("worker_id", "TEXT"), ("heartbeat_at", "TEXT"),
                            ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
                            ("progress", "TEXT NOT NULL DEFAULT '{}'")):
            if column not in run_cols:
                db.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs(owner_id, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, heartbeat_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_id)")

    # ------------------------------------------------------------------ users & activity
    def upsert_user(self, user_id: str, email: str | None, name: str | None, picture: str | None = None,
                    provider: str = "google") -> None:
        stamp = now().isoformat()
        with self.backend.session() as db:
            db.execute("INSERT INTO users (id, email, name, picture, provider, created_at, last_login) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET email = excluded.email, "
                       "name = excluded.name, picture = excluded.picture, last_login = excluded.last_login",
                       (user_id, email, name, picture, provider, stamp, stamp))

    def get_user(self, user_id: str) -> dict | None:
        with self.backend.session() as db:
            return db.one("SELECT * FROM users WHERE id = ?", (user_id,))

    def log_activity(self, owner_id: str, kind: str, run_id: str | None = None, detail: dict | None = None) -> None:
        with self.backend.session() as db:
            db.execute("INSERT INTO activity (owner_id, kind, run_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                       (owner_id, kind, run_id, json.dumps(detail or {}, default=str), now().isoformat()))

    def list_activity(self, owner_id: str, limit: int = 200) -> list[dict]:
        with self.backend.session() as db:
            rows = db.rows("SELECT kind, run_id, detail, created_at FROM activity WHERE owner_id = ? "
                           "ORDER BY id DESC LIMIT ?", (owner_id, limit))
        return [dict(r, detail=json.loads(r["detail"] or "{}")) for r in rows]

    def user_stats(self) -> list[dict]:
        """Usage per user for the admin view: counts only, never content."""
        with self.backend.session() as db:
            return db.rows(
                "SELECT u.id, u.email, u.name, u.last_login, "
                "(SELECT COUNT(*) FROM runs r WHERE r.owner_id = u.id) AS runs, "
                "(SELECT COUNT(*) FROM runs r WHERE r.owner_id = u.id AND r.mode = 'live') AS live_runs, "
                "(SELECT COUNT(*) FROM documents d WHERE d.owner_id = u.id) AS documents "
                "FROM users u ORDER BY u.last_login DESC")

    # ------------------------------------------------------------------ runs
    def create_run(self, run_id: str, request: ResearchRequest, owner_id: str = LOCAL_OWNER, status: str = "running",
                   worker_id: str | None = None) -> None:
        with self.backend.session() as db:
            db.execute("INSERT INTO runs (id, owner_id, question, status, mode, created_at, parent_run_id, request, "
                       "worker_id, heartbeat_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (run_id, owner_id, request.question, status, request.mode.value, now().isoformat(),
                        request.parent_run_id, request.model_dump_json(), worker_id, utc_now().isoformat()))

    def save_run_results(self, run_id: str, state: dict, status: str, owner_id: str = LOCAL_OWNER,
                         events_persisted: bool = False) -> None:
        """Write the final run; returns False (and writes nothing) if the run was deleted while it executed.

        With `events_persisted`, the event log already written by an EventSink is kept.
        """
        report: Report | None = state.get("report")
        exec_summary = report.section("executive_summary").content if report and report.section("executive_summary") else ""
        counts = {"sources": len(state.get("sources", [])), "claims": len(state.get("claims", [])),
                  "contradictions": len(state.get("contradictions", [])), "insights": len(state.get("insights", [])),
                  "charts": len(state.get("charts", [])), "iterations": state.get("iteration", 0) + 1, "gaps": len(state.get("gaps", []))}
        with self.backend.session() as db:
            existing = db.one("SELECT owner_id FROM runs WHERE id = ?", (run_id,))
            if existing and existing["owner_id"] != owner_id:
                raise PermissionError("This run belongs to another user.")
            if not existing:  # deleted while it executed (e.g. "Delete my data"): do not bring it back
                return False
            db.execute(
                "UPDATE runs SET status = ?, completed_at = ?, plan = ?, usage = ?, statuses = ?, stop_reason = ?, "
                "summary = ?, warnings = ?, errors = ?, provider_notes = ?, counts = ? WHERE id = ? AND owner_id = ?",
                (status, now().isoformat(), _dump(state["plan"]) if state.get("plan") else None,
                 _dump(state["usage"]) if state.get("usage") else None,
                 json.dumps({k: v.model_dump(mode="json") for k, v in state.get("statuses", {}).items()}),
                 state.get("stop_reason", ""), exec_summary[:4000],
                 json.dumps(list(dict.fromkeys(state.get("warnings", [])))), json.dumps(state.get("errors", [])),
                 json.dumps(state.get("provider_notes", [])), json.dumps(counts), run_id, owner_id))
            db.execute("UPDATE runs SET visualization_notes = ? WHERE id = ? AND owner_id = ?",
                       (json.dumps(state.get("visualization_notes", [])), run_id, owner_id))
            for table in RUN_CHILD_TABLES:
                if table != "qa_messages" and not (events_persisted and table == "events"):
                    db.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            db.insert_many("sources", ("run_id", "id", "title", "url", "source_type", "origin", "published_date", "data"), [
                (run_id, s.id, s.title, s.url, s.source_type.value, s.origin.value,
                 s.published_date.isoformat() if s.published_date else None, _dump(s)) for s in state.get("sources", [])])
            db.insert_many("passages", ("run_id", "id", "source_id", "location", "data"), [
                (run_id, p.id, p.source_id, p.location, _dump(p)) for p in state.get("passages", [])])
            db.insert_many("assessments", ("run_id", "source_id", "data"), [
                (run_id, sid, _dump(a)) for sid, a in state.get("assessments", {}).items()])
            db.insert_many("claims", ("run_id", "id", "source_id", "passage_id", "subquestion_id", "claim_type", "data"), [
                (run_id, c.id, c.source_id, c.passage_id, c.subquestion_id, c.claim_type.value, _dump(c))
                for c in state.get("claims", [])])
            db.insert_many("matrix_rows", ("run_id", "id", "subquestion_id", "agreement", "data"), [
                (run_id, r.id, r.subquestion_id, r.agreement.value, _dump(r)) for r in state.get("matrix", [])])
            db.insert_many("evidence_links", ("run_id", "claim_id", "row_id", "source_id", "relation", "data"), [
                (run_id, l.claim_id, l.row_id, l.source_id, l.relation.value, _dump(l)) for l in state.get("links", [])])
            db.insert_many("contradictions", ("run_id", "id", "claim_a_id", "claim_b_id", "data"), [
                (run_id, c.id, c.claim_a_id, c.claim_b_id, _dump(c)) for c in state.get("contradictions", [])])
            db.insert_many("gaps", ("run_id", "id", "data"), [(run_id, g.id, _dump(g)) for g in state.get("gaps", [])])
            db.insert_many("insights", ("run_id", "id", "kind", "label", "data"), [
                (run_id, i.id, i.kind.value, i.label.value, _dump(i)) for i in state.get("insights", [])])
            db.insert_many("charts", ("run_id", "id", "data"), [
                (run_id, c.id, _dump(c)) for c in state.get("charts", [])])
            if report:
                db.execute("INSERT INTO reports (run_id, title, data, created_at) VALUES (?, ?, ?, ?)",
                           (run_id, report.title, _dump(report), report.created_at.isoformat()))
            if not events_persisted:
                self._insert_events(db, run_id, state.get("events", []), 0)
            db.insert_many("search_log", ("run_id", "seq", "provider", "query", "status", "data"), [
                (run_id, n, r.provider, r.query, r.status, _dump(r)) for n, r in enumerate(state.get("search_log", []))])
        return True

    @staticmethod
    def _summary(r: dict) -> RunSummary:
        return RunSummary(id=r["id"], question=r["question"], status=r["status"], mode=r["mode"],
                          created_at=r["created_at"], completed_at=r["completed_at"], summary=r["summary"] or "",
                          counts=json.loads(r["counts"] or "{}"), parent_run_id=r["parent_run_id"],
                          depth=str(json.loads(r.get("request") or "{}").get("depth", "")))

    def list_runs(self, owner_id: str = LOCAL_OWNER, limit: int = 100) -> list[RunSummary]:
        with self.backend.session() as db:
            rows = db.rows("SELECT id, question, status, mode, created_at, completed_at, summary, counts, parent_run_id, request "
                           "FROM runs WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?", (owner_id, limit))
        return [self._summary(r) for r in rows]

    def owns_run(self, run_id: str, owner_id: str) -> bool:
        with self.backend.session() as db:
            return db.one("SELECT 1 AS ok FROM runs WHERE id = ? AND owner_id = ?", (run_id, owner_id)) is not None

    def load_run(self, run_id: str, owner_id: str = LOCAL_OWNER) -> RunBundle | None:
        """Returns None if the run doesn't exist OR belongs to someone else (no existence leak)."""
        with self.backend.session() as db:
            run = db.one("SELECT * FROM runs WHERE id = ? AND owner_id = ?", (run_id, owner_id))
            if not run:
                return None

            def data(table: str, order: str = "rowid") -> list[str]:
                return [r["data"] for r in db.rows(f"SELECT data FROM {table} WHERE run_id = ? ORDER BY {order}", (run_id,))]
            report_row = db.one("SELECT data FROM reports WHERE run_id = ?", (run_id,))
            return RunBundle(
                summary=self._summary(run), request=ResearchRequest.model_validate_json(run["request"]),
                plan=ResearchPlan.model_validate_json(run["plan"]) if run["plan"] else None,
                usage=BudgetUsage.model_validate_json(run["usage"]) if run["usage"] else None,
                statuses={k: AgentStatus.model_validate(v) for k, v in json.loads(run["statuses"] or "{}").items()},
                stop_reason=run["stop_reason"] or "",
                sources=[Source.model_validate_json(d) for d in data("sources")],
                passages=[Passage.model_validate_json(d) for d in data("passages")],
                assessments={a.source_id: a for a in (SourceAssessment.model_validate_json(d) for d in data("assessments"))},
                claims=[Claim.model_validate_json(d) for d in data("claims")],
                matrix=[MatrixRow.model_validate_json(d) for d in data("matrix_rows")],
                links=[EvidenceLink.model_validate_json(d) for d in data("evidence_links")],
                contradictions=[Contradiction.model_validate_json(d) for d in data("contradictions")],
                gaps=[ResearchGap.model_validate_json(d) for d in data("gaps")],
                insights=[Insight.model_validate_json(d) for d in data("insights")],
                charts=[EvidenceChart.model_validate_json(d) for d in data("charts")],
                visualization_notes=json.loads(run.get("visualization_notes") or "[]"),
                report=Report.model_validate_json(report_row["data"]) if report_row else None,
                events=[AgentEvent.model_validate_json(d) for d in data("events", "seq")],
                search_log=[json.loads(d) for d in data("search_log", "seq")],
                warnings=json.loads(run["warnings"] or "[]"), errors=json.loads(run["errors"] or "[]"),
                provider_notes=json.loads(run["provider_notes"] or "[]"))

    def delete_run(self, run_id: str, owner_id: str = LOCAL_OWNER, allow_active: bool = False) -> bool:
        """Delete a run and its data. Queued/running runs are kept unless `allow_active` (stop them first)."""
        with self.backend.session() as db:
            row = db.one("SELECT status FROM runs WHERE id = ? AND owner_id = ?", (run_id, owner_id))
            if not row or (row["status"] in ACTIVE_STATUSES and not allow_active):
                return False
            for table in RUN_CHILD_TABLES:
                db.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            db.execute("DELETE FROM runs WHERE id = ? AND owner_id = ?", (run_id, owner_id))
        self._drop_vectors(run_id)
        return True

    def _drop_vectors(self, run_id: str) -> None:
        """Best effort: the database delete has already happened, so a Pinecone error is logged, not raised."""
        if self.on_run_deleted is None:
            return
        try:
            self.on_run_deleted(run_id)
        except Exception:
            logger.warning("Could not delete the Pinecone vectors of run %s.", run_id, exc_info=True)

    # ------------------------------------------------------------------ live progress (durable execution)
    @staticmethod
    def _insert_events(db, run_id: str, events: list[AgentEvent], start_seq: int) -> None:
        db.insert_many("events", ("run_id", "seq", "timestamp", "agent", "status", "message", "data"), [
            (run_id, start_seq + n, e.timestamp.isoformat(), e.agent, e.status, e.message, _dump(e))
            for n, e in enumerate(events)])

    def update_progress(self, run_id: str, owner_id: str, progress: dict | None = None,
                        events: list[AgentEvent] = (), start_seq: int = 0, worker_id: str | None = None) -> ProgressAck:
        """Heartbeat (plus optional progress snapshot and events) in one transaction.

        The result is falsy when the run is no longer active (finished, reaped or deleted), so the caller can stop,
        and reports whether the owner asked the run to stop.
        """
        with self.backend.session() as db:
            changed = db.execute(
                "UPDATE runs SET heartbeat_at = ?, worker_id = COALESCE(?, worker_id), "
                "progress = COALESCE(?, progress) WHERE id = ? AND owner_id = ? AND status IN ('queued', 'running')",
                (utc_now().isoformat(), worker_id, json.dumps(progress) if progress is not None else None, run_id, owner_id))
            if not changed:
                return ProgressAck(active=False)
            if events:
                self._insert_events(db, run_id, list(events), start_seq)
            row = db.one("SELECT cancel_requested FROM runs WHERE id = ?", (run_id,))
        return ProgressAck(active=True, cancel_requested=bool(row and row["cancel_requested"]))

    def get_progress(self, run_id: str, owner_id: str, after_seq: int = -1) -> RunProgress | None:
        """Status, progress snapshot and events newer than `after_seq`; None for unknown or foreign runs."""
        with self.backend.session() as db:
            run = db.one("SELECT id, question, status, mode, created_at, heartbeat_at, cancel_requested, progress, "
                         "errors, request FROM runs WHERE id = ? AND owner_id = ?", (run_id, owner_id))
            if not run:
                return None
            rows = db.rows("SELECT seq, data FROM events WHERE run_id = ? AND seq > ? ORDER BY seq",
                           (run_id, after_seq))
        return RunProgress(
            run_id=run["id"], question=run["question"], status=run["status"], mode=run["mode"],
            depth=str(json.loads(run["request"] or "{}").get("depth", "")), created_at=run["created_at"],
            heartbeat_at=run["heartbeat_at"], cancel_requested=bool(run["cancel_requested"]),
            progress=json.loads(run["progress"] or "{}"), errors=json.loads(run["errors"] or "[]"),
            events=[AgentEvent.model_validate_json(r["data"]) for r in rows],
            last_seq=rows[-1]["seq"] if rows else after_seq)

    def request_cancel(self, run_id: str, owner_id: str) -> bool:
        """Stop a run: a queued run is cancelled at once; a running one is flagged and its worker finishes early.

        False if the run is not active or not owned by `owner_id`.
        """
        with self.backend.session() as db:
            if db.execute("UPDATE runs SET status = 'cancelled', cancel_requested = 1, completed_at = ?, "
                          "stop_reason = 'Stopped by user before it started.' WHERE id = ? AND owner_id = ? "
                          "AND status = 'queued'", (now().isoformat(), run_id, owner_id)):
                return True
            return bool(db.execute("UPDATE runs SET cancel_requested = 1 WHERE id = ? AND owner_id = ? "
                                   "AND status = 'running'", (run_id, owner_id)))

    def start_run(self, run_id: str, owner_id: str, worker_id: str) -> bool:
        """Claim a queued run for execution. False if it was cancelled, reaped or deleted while it waited."""
        with self.backend.session() as db:
            return bool(db.execute("UPDATE runs SET status = 'running', worker_id = ?, heartbeat_at = ? "
                                   "WHERE id = ? AND owner_id = ? AND status = 'queued'",
                                   (worker_id, utc_now().isoformat(), run_id, owner_id)))

    def fail_run(self, run_id: str, owner_id: str, error: str) -> None:
        """Mark an active run failed when its worker could not even produce a partial result."""
        with self.backend.session() as db:
            db.execute("UPDATE runs SET status = 'failed', completed_at = ?, errors = ? WHERE id = ? AND owner_id = ? "
                       "AND status IN ('queued', 'running')",
                       (now().isoformat(), json.dumps([error[:500]]), run_id, owner_id))

    def heartbeat_runs(self, run_ids: list[str]) -> None:
        """Keep runs that wait in this process's queue from being reaped as abandoned."""
        if not run_ids:
            return
        marks = ",".join("?" * len(run_ids))
        with self.backend.session() as db:
            db.execute(f"UPDATE runs SET heartbeat_at = ? WHERE status = 'queued' AND id IN ({marks})",
                       [utc_now().isoformat(), *run_ids])

    def is_cancel_requested(self, run_id: str) -> bool:
        with self.backend.session() as db:
            row = db.one("SELECT cancel_requested FROM runs WHERE id = ?", (run_id,))
        return bool(row and row["cancel_requested"])

    def count_active_runs(self, owner_id: str | None = None) -> int:
        """Queued or running runs, for one owner or (None) across all owners."""
        sql = "SELECT COUNT(*) AS n FROM runs WHERE status IN ('queued', 'running')"
        with self.backend.session() as db:
            row = db.one(sql + " AND owner_id = ?", (owner_id,)) if owner_id else db.one(sql)
        return int(row["n"])

    def reap_stale_runs(self, stale_after_seconds: float) -> list[str]:
        """Fail active runs whose worker stopped sending heartbeats (process stopped or crashed).

        Runs with a recent heartbeat are left alone, even when another app instance owns them.
        """
        cutoff = (utc_now() - timedelta(seconds=stale_after_seconds)).isoformat()  # heartbeats are stored in UTC
        stale = "status IN ('queued', 'running') AND (heartbeat_at IS NULL OR heartbeat_at < ?)"
        with self.backend.session() as db:
            ids = [r["id"] for r in db.rows(f"SELECT id FROM runs WHERE {stale}", (cutoff,))]
            if ids:
                db.execute(f"UPDATE runs SET status = 'failed', completed_at = ?, errors = ? WHERE {stale}",
                           (now().isoformat(), json.dumps([INTERRUPTED_ERROR]), cutoff))
        return ids

    def purge_guest_data(self, older_than_hours: int = 24) -> int:
        """Guest (not signed-in) demo runs are temporary: remove those older than the cutoff."""
        cutoff = (datetime.now() - timedelta(hours=older_than_hours)).isoformat()
        with self.backend.session() as db:
            ids = [r["id"] for r in db.rows("SELECT id FROM runs WHERE owner_id LIKE 'guest:%' AND created_at < ?", (cutoff,))]
            for run_id in ids:
                for table in RUN_CHILD_TABLES:
                    db.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
                db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            db.execute("DELETE FROM activity WHERE owner_id LIKE 'guest:%' AND created_at < ?", (cutoff,))
        for run_id in ids:
            self._drop_vectors(run_id)
        return len(ids)

    def delete_user_data(self, owner_id: str) -> int:
        """'Delete my data': every run (with its evidence), document, activity entry and preference of a user."""
        runs = [r.id for r in self.list_runs(owner_id, limit=100_000)]
        for run_id in runs:
            self.request_cancel(run_id, owner_id)  # a still-running worker stops, and its final save is skipped
            self.delete_run(run_id, owner_id, allow_active=True)
        with self.backend.session() as db:
            db.execute("DELETE FROM documents WHERE owner_id = ?", (owner_id,))
            db.execute("DELETE FROM activity WHERE owner_id = ?", (owner_id,))
            db.execute("DELETE FROM settings WHERE key LIKE ?", (f"user:{owner_id}:%",))
            db.execute("DELETE FROM users WHERE id = ?", (owner_id,))
        return len(runs)

    # ------------------------------------------------------------------ Q&A history (scoped through the run's owner)
    def save_qa(self, run_id: str, question: str, answer: dict, owner_id: str = LOCAL_OWNER) -> None:
        if not self.owns_run(run_id, owner_id):
            raise PermissionError("This run belongs to another user.")
        with self.backend.session() as db:
            db.execute("INSERT INTO qa_messages (run_id, question, data, created_at) VALUES (?, ?, ?, ?)",
                       (run_id, question, json.dumps(answer, default=str), now().isoformat()))

    def list_qa(self, run_id: str, owner_id: str = LOCAL_OWNER) -> list[dict]:
        if not self.owns_run(run_id, owner_id):
            return []
        with self.backend.session() as db:
            rows = db.rows("SELECT question, data, created_at FROM qa_messages WHERE run_id = ? ORDER BY id", (run_id,))
        return [{"question": r["question"], "answer": json.loads(r["data"]), "created_at": r["created_at"]} for r in rows]

    # ------------------------------------------------------------------ documents (per owner)
    def save_document(self, filename: str, title: str, kind: str, data: bytes, sections: list[tuple[str, str]],
                      warnings: list[str], owner_id: str = LOCAL_OWNER, metadata: dict | None = None) -> tuple[str, bool]:
        """Returns (document id, created). The same file is stored once per owner."""
        digest = hashlib.sha256(data).hexdigest()
        with self.backend.session() as db:
            existing = db.one("SELECT id FROM documents WHERE owner_id = ? AND sha256 = ?", (owner_id, digest))
            if existing:
                if metadata:
                    db.execute("UPDATE documents SET metadata = ? WHERE id = ? AND owner_id = ?",
                               (json.dumps(metadata), existing["id"], owner_id))
                return existing["id"], False
            doc_id = new_id("doc")
            db.execute("INSERT INTO documents (id, owner_id, filename, title, kind, sha256, size_bytes, sections, warnings, "
                       "uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (doc_id, owner_id, filename, title, kind, digest, len(data), json.dumps(sections),
                        json.dumps(warnings), now().isoformat()))
            db.execute("UPDATE documents SET metadata = ? WHERE id = ? AND owner_id = ?",
                       (json.dumps(metadata or {}), doc_id, owner_id))
            return doc_id, True

    def list_documents(self, owner_id: str = LOCAL_OWNER) -> list[dict]:
        with self.backend.session() as db:
            rows = db.rows("SELECT id, filename, title, kind, size_bytes, warnings, uploaded_at, sections "
                           "FROM documents WHERE owner_id = ? ORDER BY uploaded_at DESC", (owner_id,))
        return [{"id": r["id"], "filename": r["filename"], "title": r["title"], "kind": r["kind"],
                 "size_bytes": r["size_bytes"], "warnings": json.loads(r["warnings"]), "uploaded_at": r["uploaded_at"],
                 "sections": len(json.loads(r["sections"]))} for r in rows]

    def get_documents(self, ids: list[str], owner_id: str = LOCAL_OWNER) -> list[dict]:
        if not ids:
            return []
        with self.backend.session() as db:
            rows = db.rows(f"SELECT * FROM documents WHERE owner_id = ? AND id IN ({','.join('?' * len(ids))})",
                           [owner_id, *ids])
        return [{"id": r["id"], "filename": r["filename"], "title": r["title"], "kind": r["kind"],
                 "sections": [tuple(s) for s in json.loads(r["sections"])],
                 "metadata": json.loads(r.get("metadata") or "{}") } for r in rows]

    def delete_document(self, doc_id: str, owner_id: str = LOCAL_OWNER) -> None:
        with self.backend.session() as db:
            db.execute("DELETE FROM documents WHERE id = ? AND owner_id = ?", (doc_id, owner_id))

    # ------------------------------------------------------------------ search cache (shared: public search results)
    def get_cached(self, key: str, ttl_hours: int = 24) -> list[dict] | None:
        cutoff = (datetime.now() - timedelta(hours=ttl_hours)).isoformat()
        with self.backend.session() as db:
            row = db.one("SELECT results FROM search_cache WHERE key = ? AND created_at >= ?", (key, cutoff))
        return json.loads(row["results"]) if row else None

    def put_cached(self, key: str, provider: str, query: str, results: list[dict]) -> None:
        with self.backend.session() as db:
            db.execute("INSERT OR REPLACE INTO search_cache (key, provider, query, results, created_at) VALUES (?, ?, ?, ?, ?)",
                       (key, provider, query, json.dumps(results, default=str), now().isoformat()))

    def clear_cache(self) -> int:
        with self.backend.session() as db:
            return db.execute("DELETE FROM search_cache")

    def cache_size(self) -> int:
        with self.backend.session() as db:
            return db.one("SELECT COUNT(*) AS n FROM search_cache")["n"]

    # ------------------------------------------------------------------ settings (non-secret only)
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.backend.session() as db:
            row = db.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        if any(word in key.lower() for word in ("key", "secret", "token", "password")):
            raise ValueError("Secrets must be configured in .env or Streamlit Secrets, not stored in the database.")
        with self.backend.session() as db:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # ------------------------------------------------------------------ profile (personal, optional)
    def get_profile(self, owner_id: str) -> UserProfile:
        stored = self.get_user_setting(owner_id, "profile")
        try:
            return UserProfile.model_validate(stored) if stored else UserProfile()
        except ValueError:  # an older or damaged record: start again rather than fail the page
            return UserProfile()

    def save_profile(self, owner_id: str, profile: UserProfile) -> None:
        self.set_user_setting(owner_id, "profile", profile.model_dump())

    def get_user_setting(self, owner_id: str, key: str, default: Any = None) -> Any:
        return self.get_setting(f"user:{owner_id}:{key}", default)

    def set_user_setting(self, owner_id: str, key: str, value: Any) -> None:
        self.set_setting(f"user:{owner_id}:{key}", value)
