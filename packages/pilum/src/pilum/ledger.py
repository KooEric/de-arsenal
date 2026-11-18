"""The dispatch ledger: what each destination has already been told.

One SQLite file per dispatch (`<state_dir>/<name>.pilum.db`), separate from the
pipeline state databases so that installing an experimental tool migrates
nothing else (PL-ADR-3). The ledger is the source of truth for "already sent":
a row is written only after the destination answered 2xx (PL-ADR-2).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arsenal_core.errors import FatalError

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    record_key   TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    dispatch_id  TEXT NOT NULL,
    dispatched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    dispatch_id TEXT PRIMARY KEY,
    record_key  TEXT NOT NULL,
    attempts    INTEGER NOT NULL,
    last_error  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dlq (
    dispatch_id TEXT PRIMARY KEY,
    record_key  TEXT NOT NULL,
    action      TEXT NOT NULL,
    attempts    INTEGER NOT NULL,
    last_error  TEXT NOT NULL,
    failed_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    finished_at TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    report_json TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def ledger_path(state_dir: str, name: str) -> Path:
    return Path(state_dir) / f"{name}.pilum.db"


@dataclass(frozen=True)
class DeadLetter:
    dispatch_id: str
    record_key: str
    action: str
    attempts: int
    last_error: str
    failed_at: str


class Ledger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._con = sqlite3.connect(path)
        found = int(self._con.execute("PRAGMA user_version").fetchone()[0])
        if found > SCHEMA_VERSION:
            self._con.close()
            raise FatalError(
                f"{path} was written by a newer pilum (ledger v{found}, this is "
                f"v{SCHEMA_VERSION}); upgrade de-pilum"
            )
        self._con.executescript(_SCHEMA)
        self._con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    # --- reads -----------------------------------------------------------

    def entries(self) -> dict[str, str]:
        """record_key → content_hash of the last version the destination accepted."""

        rows = self._con.execute("SELECT record_key, content_hash FROM ledger").fetchall()
        return {str(key): str(content) for key, content in rows}

    def size(self) -> int:
        return int(self._con.execute("SELECT count(*) FROM ledger").fetchone()[0])

    def dead_ids(self) -> set[str]:
        return {str(row[0]) for row in self._con.execute("SELECT dispatch_id FROM dlq")}

    def dead_letters(self, limit: int | None = None) -> list[DeadLetter]:
        sql = (
            "SELECT dispatch_id, record_key, action, attempts, last_error, failed_at "
            "FROM dlq ORDER BY failed_at, dispatch_id"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [DeadLetter(*row) for row in self._con.execute(sql).fetchall()]

    def last_run(self) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT finished_at, ok, report_json FROM runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {"finished_at": row[0], "ok": bool(row[1]), "report": json.loads(row[2])}

    def last_success_at(self) -> str | None:
        row = self._con.execute("SELECT max(finished_at) FROM runs WHERE ok = 1").fetchone()
        return None if row is None or row[0] is None else str(row[0])

    # --- writes (each commits: one record's outcome is durable on its own) ---

    def record_sent(self, record_key: str, content_hash: str, dispatch_id: str) -> None:
        """The destination accepted this version. Clears older failures of the key."""

        with self._con:
            self._con.execute(
                "INSERT INTO ledger VALUES (?, ?, ?, ?) ON CONFLICT(record_key) DO UPDATE SET "
                "content_hash = excluded.content_hash, dispatch_id = excluded.dispatch_id, "
                "dispatched_at = excluded.dispatched_at",
                (record_key, content_hash, dispatch_id, _now()),
            )
            self._clear_failures(record_key)

    def record_deleted(self, record_key: str) -> None:
        with self._con:
            self._con.execute("DELETE FROM ledger WHERE record_key = ?", (record_key,))
            self._clear_failures(record_key)

    def _clear_failures(self, record_key: str) -> None:
        self._con.execute("DELETE FROM attempts WHERE record_key = ?", (record_key,))
        self._con.execute("DELETE FROM dlq WHERE record_key = ?", (record_key,))

    def add_attempts(self, dispatch_id: str, record_key: str, count: int, error: str) -> int:
        """Add failed tries for one dispatch; returns its total over all runs."""

        with self._con:
            self._con.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?) ON CONFLICT(dispatch_id) DO UPDATE "
                "SET attempts = attempts + excluded.attempts, last_error = excluded.last_error, "
                "updated_at = excluded.updated_at",
                (dispatch_id, record_key, count, error, _now()),
            )
        row = self._con.execute(
            "SELECT attempts FROM attempts WHERE dispatch_id = ?", (dispatch_id,)
        ).fetchone()
        return int(row[0])

    def park(
        self, dispatch_id: str, record_key: str, action: str, attempts: int, error: str
    ) -> None:
        with self._con:
            self._con.execute(
                "INSERT OR REPLACE INTO dlq VALUES (?, ?, ?, ?, ?, ?)",
                (dispatch_id, record_key, action, attempts, error, _now()),
            )
            self._con.execute("DELETE FROM attempts WHERE dispatch_id = ?", (dispatch_id,))

    def requeue(self, limit: int | None = None) -> int:
        """Take dead letters out of the queue so the next run tries them again."""

        ids = [letter.dispatch_id for letter in self.dead_letters(limit)]
        with self._con:
            self._con.executemany("DELETE FROM dlq WHERE dispatch_id = ?", [(i,) for i in ids])
        return len(ids)

    def reset(self) -> None:
        with self._con:
            for table in ("ledger", "attempts", "dlq"):
                self._con.execute(f"DELETE FROM {table}")

    def record_run(self, report: dict[str, Any], *, ok: bool) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO runs (finished_at, ok, report_json) VALUES (?, ?, ?)",
                (_now(), int(ok), json.dumps(report, sort_keys=True)),
            )


class RunLock:
    """One sender per dispatch. Two concurrent runs would each send every record.

    Holds an exclusive SQLite transaction on a sidecar file: the operating system
    drops it when the process dies, so a crashed run never leaves a stale lock.
    """

    def __init__(self, ledger_file: Path) -> None:
        self._path = ledger_file.with_suffix(".lock")
        self._con: sqlite3.Connection | None = None

    def __enter__(self) -> RunLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._path, timeout=0, isolation_level=None)
        try:
            con.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as error:
            con.close()
            raise FatalError(
                "another pilum process is working on this dispatch; wait for it to finish"
            ) from error
        self._con = con
        return self

    def __exit__(self, *exc: object) -> None:
        if self._con is not None:
            self._con.rollback()
            self._con.close()
            self._con = None
