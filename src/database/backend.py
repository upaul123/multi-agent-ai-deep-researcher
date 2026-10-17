"""Database backends: local SQLite file, or Turso (hosted libSQL) via an embedded replica.

Both backends expose the same tiny interface (`rows`, `one`, `execute`, `insert_many`, `script`) and return rows as
plain dicts, so the repository is backend-agnostic.

Turso notes:
- We use an *embedded replica*: a local file kept in sync with the Turso primary. Reads are local (fast); writes are
  sent to the primary, so data survives app restarts and is shared by every app instance.
- Every write statement is a network round trip, so bulk saves use chunked multi-row INSERTs.
- A single connection is shared and guarded by a lock (libsql connections are not thread-safe).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_PARAMS_PER_STATEMENT = 900  # stays under SQLite's historical 999-variable limit


class Session:
    """A unit of work on one connection (committed when the context exits without error)."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        cursor = self.conn.execute(sql, tuple(params))
        return getattr(cursor, "rowcount", -1) or 0

    def rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        cursor = self.conn.execute(sql, tuple(params))
        # Lowercased: Turso's server returns keyword-like columns in upper case (e.g. `plan` comes back as "PLAN").
        names = [d[0].lower() for d in (cursor.description or [])]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def insert_many(self, table: str, columns: Sequence[str], values: list[Sequence[Any]], verb: str = "INSERT OR IGNORE") -> None:
        """Multi-row INSERT in chunks: one statement per ~900 parameters instead of one per row."""
        if not values:
            return
        per_row = len(columns)
        chunk = max(1, MAX_PARAMS_PER_STATEMENT // per_row)
        placeholder = "(" + ", ".join("?" * per_row) + ")"
        for start in range(0, len(values), chunk):
            part = values[start:start + chunk]
            sql = f"{verb} INTO {table} ({', '.join(columns)}) VALUES " + ", ".join([placeholder] * len(part))
            self.conn.execute(sql, tuple(v for row in part for v in row))

    def script(self, sql: str) -> None:
        self.conn.executescript(sql)


class SQLiteBackend:
    name = "sqlite"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield Session(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def describe(self) -> str:
        return f"local SQLite file {self.path}"


class TursoBackend:
    name = "turso"

    def __init__(self, url: str, auth_token: str, replica_path: Path, sync_interval: int = 60):
        import libsql

        self.url = url
        replica_path = Path(replica_path)
        replica_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = libsql.connect(str(replica_path), sync_url=url, auth_token=auth_token,
                                    sync_interval=sync_interval, _check_same_thread=False)
        self._conn.sync()  # pull the latest data from the primary on startup
        self._conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._lock:
            try:
                yield Session(self._conn)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def sync(self) -> None:
        with self._lock:
            self._conn.sync()

    def describe(self) -> str:
        host = self.url.split("://", 1)[-1]
        return f"Turso database {host} (embedded replica)"


def make_backend(db_path: Path, turso_url: str | None = None, turso_token: str | None = None):
    """Turso when both URL and token are configured; otherwise a local SQLite file."""
    if turso_url and turso_token:
        return TursoBackend(turso_url, turso_token, replica_path=db_path.with_name("turso-replica.db"))
    return SQLiteBackend(db_path)
