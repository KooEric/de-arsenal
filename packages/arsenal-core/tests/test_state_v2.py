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


def _commit_batch(store: StateStore, lease: object, batch_id: str, end: int, rows: int) -> None:
    store.plan_stream_batch(
        batch_id=batch_id,
        pipeline="p",
        stream_name="orders",
        revision="r",
        start_position={"offset": end - 1},
        end_position={"offset": end},
        event_hash=f"events-{end}",
    )
    store.commit_stream_batch(
        batch_id,
        lease=lease,  # type: ignore[arg-type]
        checkpoint_position={"offset": end},
        watermark="2026-08-22T00:00:00+00:00",
        output_proof={"rows": rows},
        rows=rows,
        bytes=rows * 10,
        late_rows=1,
    )


def test_lease_record_reports_holder_and_expiry(tmp_path: Path) -> None:
    clock = Clock()
    store = StateStore(tmp_path / "state.db", clock=clock)
    assert store.lease("stream:p:orders") is None

    token = store.acquire_lease("stream:p:orders", "runner-a", 30)
    assert token is not None
    record = store.lease("stream:p:orders")

    assert record is not None
    assert (record.owner_id, record.fence) == ("runner-a", token.fence)
    assert record.expires_at == clock() + timedelta(seconds=30)
    assert record.is_expired(clock()) is False
    clock.advance(31)
    assert record.is_expired(clock()) is True


def test_stream_batch_summary_counts_ledger_by_status(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", clock=Clock())
    lease = store.acquire_lease("stream:p:orders", "a", 30)
    _commit_batch(store, lease, "b1", 1, rows=5)
    _commit_batch(store, lease, "b2", 2, rows=7)
    store.plan_stream_batch(
        batch_id="b3",
        pipeline="p",
        stream_name="orders",
        revision="r",
        start_position={"offset": 2},
        end_position={"offset": 3},
        event_hash="events-3",
    )

    summary = store.stream_batch_summary("p", "orders")

    assert summary.by_status == {"committed": 2, "planned": 1}
    assert (summary.rows, summary.bytes, summary.late_rows) == (12, 120, 2)
    empty = store.stream_batch_summary("p", "other")
    assert empty.by_status == {} and empty.rows == 0


def test_reset_checkpoint_bumps_generation_and_clears_position(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", clock=Clock())
    lease = store.acquire_lease("stream:p:orders", "a", 30)
    assert lease is not None
    _commit_batch(store, lease, "b1", 1, rows=5)

    reset = store.reset_checkpoint("p", "orders", lease=lease)

    assert reset.generation == 1
    assert reset.position == {}
    assert reset.watermark is None and reset.last_batch_id is None
    assert store.checkpoint("p", "orders") == reset
    assert store.stream_batch("b1").status == "committed"  # type: ignore[union-attr]
    assert store.reset_checkpoint("p", "orders", lease=lease).generation == 2


def test_reset_checkpoint_requires_the_live_lease(tmp_path: Path) -> None:
    clock = Clock()
    store = StateStore(tmp_path / "state.db", clock=clock)
    lease = store.acquire_lease("stream:p:orders", "a", 10)
    assert lease is not None
    _commit_batch(store, lease, "b1", 1, rows=5)
    clock.advance(11)

    with pytest.raises(LeaseLostError):
        store.reset_checkpoint("p", "orders", lease=lease)
    assert store.checkpoint("p", "orders").generation == 0  # type: ignore[union-attr]


def test_reset_without_checkpoint_creates_generation_one(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", clock=Clock())
    lease = store.acquire_lease("stream:p:fresh", "a", 10)
    assert lease is not None

    assert store.reset_checkpoint("p", "fresh", lease=lease).generation == 1


def test_v2_ledger_is_migrated_to_generation_aware_identity(tmp_path: Path) -> None:
    from arsenal_core.state.migrations import MIGRATIONS

    db = tmp_path / "v2.db"
    with sqlite3.connect(db) as conn:
        for version in (1, 2):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
        conn.execute("PRAGMA user_version=2")
        conn.execute(
            "INSERT INTO stream_batches(batch_id, pipeline, stream_name, revision, "
            "start_position_json, end_position_json, event_hash, status, created_at, updated_at) "
            "VALUES ('old', 'p', 'orders', 'r', '{}', '{\"offset\":1}', 'e', 'committed', 't', 't')"
        )

    store = StateStore(db, clock=Clock())

    assert store.schema_version == CURRENT_SCHEMA_VERSION >= 3
    assert store.stream_batch("old").generation == 0  # type: ignore[union-attr]
    replay = store.plan_stream_batch(
        batch_id="replay",
        pipeline="p",
        stream_name="orders",
        revision="r",
        start_position={},
        end_position={"offset": 1},
        event_hash="e",
        generation=1,
    )
    assert replay.generation == 1 and replay.status == "planned"
    with pytest.raises(ValueError, match="reused"):
        store.plan_stream_batch(
            batch_id="old",
            pipeline="p",
            stream_name="orders",
            revision="r",
            start_position={},
            end_position={"offset": 1},
            event_hash="e",
            generation=1,
        )
