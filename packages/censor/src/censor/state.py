"""Read Pugio run state without touching it.

Opening a state DB with `StateStore` rewrites its `-shm` file and migrates an older schema in
place, so the DB and its WAL are copied under `.arsenal/censor/tmp/` and the copy is opened.
(See the investigation notes in docs/plans/2026-11-26-censor-plan2-standard.md.)
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

import duckdb
from arsenal.dataset import status_report

from arsenal_core.errors import FatalError
from arsenal_core.state import StateStore
from censor.context import SpecFile
from censor.history import scratch_dir
from pugio.dlq import split_rejected_rows

DEFAULT_STATE_DIR: Final = ".arsenal"
_WAL_SUFFIX: Final = "-wal"
_COPY_ATTEMPTS: Final = 3


@dataclass(frozen=True)
class PipelineState:
    name: str
    units: Mapping[str, int] = field(default_factory=dict[str, int])
    rows_done: int = 0
    rejected_rows: int = 0
    last_completed_at: datetime | None = None
    lease_until: datetime | None = None  # set while another run holds the pipeline lease
    busy: bool = False  # the state could not be read consistently: a run is writing it

    @property
    def never_ran(self) -> bool:
        return not self.units and not self.busy


def pipeline_identity(root: Path, spec: SpecFile) -> tuple[str, Path] | None:
    """(pipeline name, state directory) from the raw YAML, so unset env vars do not matter."""
    raw = spec.raw or {}
    name: object = raw.get("name")
    if not isinstance(name, str) or not name:
        return None
    state_dir: object = raw.get("state_dir", DEFAULT_STATE_DIR)
    directory = Path(state_dir) if isinstance(state_dir, str | Path) else Path(DEFAULT_STATE_DIR)
    return name, directory if directory.is_absolute() else root / directory


class _StateChanging(Exception):
    """The state DB or its WAL kept changing while it was being copied."""


def _stamp(*paths: Path) -> list[tuple[int, int] | None]:
    stats = [p.stat() if p.exists() else None for p in paths]
    return [None if s is None else (s.st_size, s.st_mtime_ns) for s in stats]


def _copy_consistent(db: Path, copy: Path) -> None:
    """Copy the DB and its WAL as one snapshot: retry while a writer changes either file.

    A checkpoint between the two copies would pair an old DB with a new WAL, which SQLite
    opens without complaint and answers from with stale counts.
    """
    wal = db.with_name(db.name + _WAL_SUFFIX)
    wal_copy = copy.with_name(copy.name + _WAL_SUFFIX)
    for _ in range(_COPY_ATTEMPTS):
        before = _stamp(db, wal)
        shutil.copyfile(db, copy)
        wal_copy.unlink(missing_ok=True)
        if wal.exists():
            shutil.copyfile(wal, wal_copy)
        if _stamp(db, wal) == before:
            return
    raise _StateChanging


def _read_copy(db: Path, name: str, scratch: Path, now: datetime) -> PipelineState:
    copy = scratch / db.name
    _copy_consistent(db, copy)
    store = StateStore(copy, backup_dir=scratch / "backups")
    try:
        units = store.counts(name)
        metrics = store.pipeline_metrics(name)
        lease = store.lease(f"pipeline:{name}")
    finally:
        store.close()
    completed = metrics.last_completed_at
    return PipelineState(
        name=name,
        units=units,
        rows_done=metrics.row_count,
        last_completed_at=datetime.fromisoformat(completed) if completed else None,
        lease_until=lease.expires_at if lease is not None and not lease.is_expired(now) else None,
    )


def read_pipeline_state(root: Path, spec: SpecFile, now: datetime) -> PipelineState | None:
    """State of one pipeline, or None when it has never run (or the spec has no name).

    Raises `FatalError` when a DLQ record is unreadable.
    """
    identity = pipeline_identity(root, spec)
    if identity is None:
        return None
    name, state_dir = identity
    db = state_dir / f"{name}.db"
    if not db.is_file():
        return None
    try:
        with scratch_dir(root) as scratch:
            state = _read_copy(db, name, scratch, now)
    except (sqlite3.Error, FatalError, RuntimeError, ValueError, OSError, _StateChanging):
        # A copy taken while a run checkpoints can be torn; a newer schema cannot be read.
        return PipelineState(name=name, busy=True)
    if state.never_ran:
        return None
    rejected = split_rejected_rows(state_dir, name)
    return PipelineState(
        name=state.name,
        units=state.units,
        rows_done=state.rows_done,
        rejected_rows=rejected,
        last_completed_at=state.last_completed_at,
        lease_until=state.lease_until,
    )


DATASET_STORE: Final = Path(".arsenal") / "dataset.duckdb"  # `arsenal.dataset.STORE`, per project


@dataclass(frozen=True)
class DatasetState:
    state: str  # published | stale | failed | never_run | busy | unreadable
    has_published: bool = False
    last_success: datetime | None = None
    last_input_change: datetime | None = None
    max_age_hours: float | None = None
    error: str | None = None


def _moment(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def read_dataset_state(root: Path, now: datetime) -> DatasetState | None:
    """The `arsenal dataset` publication of a project, or None when the project has none.

    `status_report` supplies the fields; staleness is judged again with `now`, because
    `status_report` reads the wall clock and a grade must not depend on it.
    """
    store = root / DATASET_STORE
    if not store.is_file():
        return None
    try:
        report = status_report(store)
        with duckdb.connect(str(store), read_only=True) as db:
            row = db.execute(
                "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'published'"
            ).fetchone()
    except duckdb.IOException:  # locked by a running `arsenal dataset apply`
        return DatasetState(state="busy")
    except (duckdb.Error, KeyError, ValueError, TypeError):  # not a store this version wrote
        return DatasetState(state="unreadable")
    rules: object = report.get("rules")
    limit: object = cast(dict[str, Any], rules).get("max_age_hours") if rules else None
    max_age = float(limit) if isinstance(limit, int | float) else None
    try:
        changed = _moment(report.get("last_input_change"))
        succeeded = _moment(report.get("last_success"))
    except ValueError:
        return DatasetState(state="unreadable")
    state = str(report["state"])
    if state in ("published", "stale") and changed is not None:
        stale = max_age is not None and now - changed > timedelta(hours=max_age)
        state = "stale" if stale else "published"
    error: object = report.get("error")
    return DatasetState(
        state=state,
        has_published=bool(row and row[0]),
        last_success=succeeded,
        last_input_change=changed,
        max_age_hours=max_age,
        error=error if isinstance(error, str) else None,
    )
