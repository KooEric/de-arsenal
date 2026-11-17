"""A Pugio run holds a short, heartbeat-renewed lease on its pipeline."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import FileSourceSpec, ParquetSinkSpec, PipelineSpec
from arsenal_core.state import StateStore
from arsenal_core.writers import stream_writers
from pugio.runner import run_pipeline
from pugio.writer_guard import pipeline_lease, resource


def spec(tmp_path: Path) -> PipelineSpec:
    for name in ("a", "b"):
        (tmp_path / f"{name}.csv").write_text("id,v\n1,x\n2,y\n", encoding="utf-8")
    return PipelineSpec(
        name="orders",
        state_dir=str(tmp_path / ".arsenal"),
        source=FileSourceSpec(type="file", path=str(tmp_path / "*.csv")),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


def state(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / ".arsenal" / "orders.db")


def test_a_run_registers_its_parquet_sink_and_releases_the_lease(tmp_path: Path) -> None:
    pipeline = spec(tmp_path)
    run_pipeline(pipeline)

    [writer] = stream_writers(tmp_path / "out")
    assert (writer.resource, writer.pipeline) == ("pipeline:orders", "orders")
    store = state(tmp_path)
    assert store.acquire_lease(resource(pipeline), "next", 30) is not None  # free again
    store.close()


def test_a_run_refuses_while_another_holder_has_the_lease(tmp_path: Path) -> None:
    pipeline = spec(tmp_path)
    store = state(tmp_path)
    assert store.acquire_lease(resource(pipeline), "onager-compaction", 30) is not None

    with pytest.raises(FatalError, match="is busy .*onager-compaction"):
        run_pipeline(pipeline)
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())
    store.close()


def test_the_heartbeat_keeps_the_lease_through_a_slow_unit(tmp_path: Path) -> None:
    pipeline = spec(tmp_path)
    store = state(tmp_path)
    other = state(tmp_path)

    with pipeline_lease(pipeline, store, ttl_seconds=0.3, heartbeat_seconds=0.05) as check:
        time.sleep(0.6)  # two TTLs: only the heartbeat keeps it alive
        assert other.acquire_lease(resource(pipeline), "intruder", 30) is None
        check()
    assert other.acquire_lease(resource(pipeline), "intruder", 30) is not None
    store.close()
    other.close()


def test_a_lost_lease_stops_the_run_at_the_next_check(tmp_path: Path) -> None:
    pipeline = spec(tmp_path)
    store = state(tmp_path)
    other = state(tmp_path)

    with pipeline_lease(pipeline, store, ttl_seconds=0.2, heartbeat_seconds=0.05) as check:
        held = store.lease(resource(pipeline))
        assert held is not None
        other._conn.execute("DELETE FROM leases WHERE resource=?", (resource(pipeline),))  # pyright: ignore[reportPrivateUsage]
        other._conn.commit()  # pyright: ignore[reportPrivateUsage]
        assert other.acquire_lease(resource(pipeline), "takeover", 30) is not None
        time.sleep(0.2)
        with pytest.raises(FatalError, match="lost its run lease"):
            check()
    store.close()
    other.close()
