"""Several processes opening a brand-new state database at the same moment.

Two `arsenal run`s started together on a fresh project both create
``.arsenal/<name>.db``. Switching the new file to WAL and applying the first
migration used to race, and the loser crashed with a raw
``sqlite3.OperationalError: database is locked`` before the run lease could
report "busy".
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.state import StateStore
from arsenal_core.state import store as store_module
from arsenal_core.state.migrations import CURRENT_SCHEMA_VERSION, apply_migrations

PROCESSES = 8
ROUNDS = 5
WORKER_TIMEOUT_SECONDS = 60

# Each worker is a fresh interpreter that waits for the "go" file so they all
# open the database at the same moment. It needs nothing from this test module,
# so it works however pytest imported it.
_WORKER = """
import sys, time
from pathlib import Path
go, db = Path(sys.argv[1]), Path(sys.argv[2])
from arsenal_core.state import StateStore
while not go.exists():
    time.sleep(0.001)
try:
    store = StateStore(db)
    print(f"ok:{store.schema_version}")
    store.close()
except Exception as exc:
    print(f"error:{type(exc).__name__}: {exc}")
"""


def _open_concurrently(db_path: Path) -> list[str]:
    go = db_path.parent / "go"
    db_path.parent.mkdir(parents=True)
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(go), str(db_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(PROCESSES)
    ]
    go.touch()
    return [w.communicate(timeout=WORKER_TIMEOUT_SECONDS)[0].strip() for w in workers]


def test_processes_opening_a_new_state_db_together_all_succeed(tmp_path: Path) -> None:
    for round_ in range(ROUNDS):
        db_path = tmp_path / f"round{round_}" / "pipeline.db"

        outcomes = _open_concurrently(db_path)

        assert outcomes == [f"ok:{CURRENT_SCHEMA_VERSION}"] * PROCESSES, outcomes


def test_migration_skips_versions_another_connection_applied_meanwhile(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    loser = sqlite3.connect(db_path)
    winner = sqlite3.connect(db_path)

    def winner_migrates_first(_version: int) -> None:
        # Runs after `loser` read user_version=0 but before it takes the write lock.
        if winner.execute("PRAGMA user_version").fetchone()[0] == 0:
            apply_migrations(winner)

    _initial, final = apply_migrations(loser, before_migration=winner_migrates_first)

    assert final == CURRENT_SCHEMA_VERSION
    assert loser.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    loser.close()
    winner.close()


def test_a_state_db_locked_for_too_long_is_a_clean_busy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "BUSY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(store_module, "WAL_SWITCH_TIMEOUT_SECONDS", 0.0)
    db_path = tmp_path / "state.db"
    holder = sqlite3.connect(db_path, timeout=0)
    holder.execute("BEGIN EXCLUSIVE")

    with pytest.raises(FatalError, match="is busy"):
        StateStore(db_path)

    holder.rollback()
    holder.close()
