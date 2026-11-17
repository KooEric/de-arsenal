import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arsenal_core.execution import ArtifactRef, OutputProof
from arsenal_core.state import LeaseLostError, StateStore
from arsenal_core.state.migrations import CURRENT_SCHEMA_VERSION


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 22, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def test_existing_v02_database_is_backed_up_and_forward_migrated(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE units(unit_id TEXT PRIMARY KEY, pipeline TEXT NOT NULL, "
            "unit_key TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, row_count INTEGER, "
            "byte_count INTEGER, duration_ms INTEGER, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO units VALUES ('u', 'p', 'k', '{}', 'done', 0, NULL, NULL, "
            "NULL, NULL, 't', 't')"
        )

    store = StateStore(db)

    assert store.schema_version == CURRENT_SCHEMA_VERSION
    assert store.status("u") == "done"
    assert {path.name.split("-")[0] for path in (tmp_path / "backups").glob("*.sqlite")} == {
        "legacy"
    }


def test_lease_fence_blocks_stale_run_and_stream_commits(tmp_path: Path) -> None:
    clock = Clock()
    store = StateStore(tmp_path / "state.db", clock=clock)
    first = store.acquire_lease("stream:p:orders", "one", 10)
    assert first is not None
    assert store.acquire_lease("stream:p:orders", "two", 10) is None

    store.create_run(
        run_id="run-1",
        pipeline="p",
        revision="r",
        mode="stream",
        executor="local",
        guarantees={},
    )
    clock.advance(11)
    second = store.acquire_lease("stream:p:orders", "two", 10)
    assert second is not None
    assert second.fence == first.fence + 1
    with pytest.raises(LeaseLostError):
        store.update_run("run-1", status="succeeded", lease=first)

    store.plan_stream_batch(
        batch_id="batch-1",
        pipeline="p",
        stream_name="orders",
        revision="r",
        start_position={"offset": 0},
        end_position={"offset": 1},
        event_hash="events",
    )
    proof = OutputProof(
        run_id="run-1",
        revision="r",
        output=ArtifactRef("file:///out", "parquet", content_hash="output"),
        input_versions=("input-1",),
        row_count=1,
        schema_hash="schema",
        plan_hash="plan",
        started_at=clock(),
        finished_at=clock(),
    )
    with pytest.raises(LeaseLostError):
        store.commit_stream_batch(
            "batch-1", lease=first, checkpoint_position={"offset": 1}, output_proof=proof
        )
    assert store.checkpoint("p", "orders") is None

    store.commit_stream_batch(
        "batch-1", lease=second, checkpoint_position={"offset": 1}, output_proof=proof, rows=1
    )
    checkpoint = store.checkpoint("p", "orders")
    assert checkpoint is not None
    assert checkpoint.position == {"offset": 1}
    assert checkpoint.last_batch_id == "batch-1"
    assert store.stream_batch("batch-1").status == "committed"  # type: ignore[union-attr]
