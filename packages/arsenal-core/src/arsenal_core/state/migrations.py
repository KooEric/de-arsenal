"""Forward-only SQLite schema migrations for :mod:`arsenal_core.state`."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

CURRENT_SCHEMA_VERSION = 3

_V1_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS units (
        unit_id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, unit_key TEXT NOT NULL,
        payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, row_count INTEGER, byte_count INTEGER, duration_ms INTEGER,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_units_pipeline_status ON units(pipeline, status)",
    """CREATE TABLE IF NOT EXISTS cursors (
        pipeline TEXT NOT NULL, source TEXT NOT NULL, cursor TEXT NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY (pipeline, source))""",
    """CREATE TABLE IF NOT EXISTS schema_snapshots (
        pipeline TEXT NOT NULL, captured_at TEXT NOT NULL, schema_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS lineage (
        pipeline TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_ref TEXT NOT NULL,
        sink_type TEXT NOT NULL, sink_ref TEXT NOT NULL, updated_at TEXT NOT NULL)""",
)

_V2_STATEMENTS = (
    """CREATE TABLE runs (
        run_id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, revision TEXT NOT NULL,
        mode TEXT NOT NULL, executor TEXT NOT NULL, status TEXT NOT NULL,
        guarantee_json TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
        external_job_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX idx_runs_pipeline_status ON runs(pipeline, status)",
    """CREATE TABLE leases (
        resource TEXT PRIMARY KEY, owner_id TEXT NOT NULL, fence INTEGER NOT NULL,
        expires_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE stream_checkpoints (
        pipeline TEXT NOT NULL, stream_name TEXT NOT NULL, generation INTEGER NOT NULL,
        position_json TEXT NOT NULL, watermark TEXT, last_batch_id TEXT,
        updated_at TEXT NOT NULL, PRIMARY KEY (pipeline, stream_name))""",
    """CREATE TABLE stream_batches (
        batch_id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, stream_name TEXT NOT NULL,
        revision TEXT NOT NULL, start_position_json TEXT NOT NULL, end_position_json TEXT NOT NULL,
        event_hash TEXT NOT NULL, status TEXT NOT NULL, output_ref_json TEXT,
        rows INTEGER, bytes INTEGER, late_rows INTEGER NOT NULL DEFAULT 0, error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE UNIQUE INDEX stream_batches_identity ON stream_batches
        (pipeline, stream_name, revision, start_position_json, end_position_json)""",
)

# v3: a checkpoint reset starts a new generation that replays the same source
# ranges, so the ledger identity must include the generation.
_V3_STATEMENTS = (
    "ALTER TABLE stream_batches ADD COLUMN generation INTEGER NOT NULL DEFAULT 0",
    "DROP INDEX stream_batches_identity",
    """CREATE UNIQUE INDEX stream_batches_identity ON stream_batches
        (pipeline, stream_name, revision, generation, start_position_json, end_position_json)""",
)

MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: _V1_STATEMENTS,
    2: _V2_STATEMENTS,
    3: _V3_STATEMENTS,
}


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    before_migration: Callable[[int], None] | None = None,
) -> tuple[int, int]:
    """Apply every pending migration in an immediate transaction per version."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"state database version {version} is newer than supported "
            f"version {CURRENT_SCHEMA_VERSION}; downgrade is not supported"
        )
    initial_version = version
    while version < CURRENT_SCHEMA_VERSION:
        target = version + 1
        if before_migration is not None:
            before_migration(version)
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have migrated between our read and the write
            # lock (two runs opening a new state db together); re-read under it.
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current >= target:
                conn.commit()
                version = current
                continue
            for statement in MIGRATIONS[target]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={target}")
            foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise RuntimeError(f"foreign key validation failed: {foreign_key_errors!r}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        version = target
    return initial_version, version
