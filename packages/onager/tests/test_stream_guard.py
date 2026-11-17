"""Onager must never rewrite the output of a live or half-committed Hasta stream."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from onager.compact import compact_dataset

from arsenal_core.state import StateStore
from arsenal_core.writers import DatasetBusyError, StreamWriter, register_stream_writer

RESOURCE = "stream:orders:orders"


def dataset_with_stream(tmp_path: Path) -> tuple[Path, StateStore]:
    dataset = tmp_path / "orders"
    dataset.mkdir()
    for index in range(3):
        pq.write_table(pa.table({"id": [index]}), dataset / f"batch-{index}.parquet")  # pyright: ignore[reportUnknownMemberType]
    db = tmp_path / "state.db"
    store = StateStore(db)
    register_stream_writer(dataset, StreamWriter(str(db), RESOURCE, "orders", "orders"))
    return dataset, store


def test_compaction_refuses_while_the_stream_runs(tmp_path: Path) -> None:
    dataset, store = dataset_with_stream(tmp_path)
    store.acquire_lease(RESOURCE, "hasta-live", 60)

    with pytest.raises(DatasetBusyError, match="hasta-live"):
        compact_dataset(dataset)
    assert len(list(dataset.glob("*.parquet"))) == 3


def test_compaction_refuses_with_uncommitted_batches(tmp_path: Path) -> None:
    dataset, store = dataset_with_stream(tmp_path)
    store.plan_stream_batch(
        batch_id="b9",
        pipeline="orders",
        stream_name="orders",
        revision="r",
        start_position={},
        end_position={"cursor": "9"},
        event_hash="e",
    )

    with pytest.raises(DatasetBusyError, match="uncommitted"):
        compact_dataset(dataset)


def test_compaction_proceeds_when_the_stream_is_stopped(tmp_path: Path) -> None:
    dataset, store = dataset_with_stream(tmp_path)

    report = compact_dataset(dataset)

    assert report.compacted is True
    assert store.lease(RESOURCE) is None  # Onager released the stream lease
