"""Iceberg sink against real Iceberg tables: a SQLite catalog and a local warehouse."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from _sink_contract import (
    assert_composite_merge_key_upsert,
    assert_in_batch_duplicate_key_keeps_last,
    assert_merge_updates_changed_rows,
    assert_two_different_batches_accumulate,
    assert_write_twice_same_unit_is_idempotent,
    make_unit,
)

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import IcebergSinkSpec, PipelineSpec
from pugio.runner import run_pipeline
from pugio.sinks import build_sink
from pugio.sinks.iceberg import UNIT_ID_PROPERTY, IcebergSink

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")


def catalog_properties(root: Path) -> dict[str, str]:
    warehouse = root / "warehouse"
    warehouse.mkdir(parents=True, exist_ok=True)
    return {
        "type": "sql",
        "uri": f"sqlite:///{(root / 'catalog.db').as_posix()}",
        "warehouse": warehouse.as_uri(),
    }


def make_sink(root: Path, table: str, merge_key: list[str] | None = None) -> IcebergSink:
    spec = IcebergSinkSpec(
        type="iceberg",
        catalog=catalog_properties(root),
        table=f"lake.{table}",
        merge_key=merge_key if merge_key is not None else ["id"],
    )
    return IcebergSink(spec)


def load(root: Path, table: str) -> Any:
    from pyiceberg.catalog import load_catalog

    return load_catalog("arsenal", **catalog_properties(root)).load_table(("lake", table))


def rows_of(root: Path, table: str) -> list[dict[str, object]]:
    return load(root, table).scan().to_arrow().to_pylist()


def test_write_twice_same_unit_row_count_unchanged(tmp_path: Path) -> None:
    assert_write_twice_same_unit_is_idempotent(
        make_sink(tmp_path, "idem"), lambda: rows_of(tmp_path, "idem")
    )


def test_merge_updates_changed_rows(tmp_path: Path) -> None:
    assert_merge_updates_changed_rows(
        make_sink(tmp_path, "merge"), lambda: rows_of(tmp_path, "merge")
    )


def test_two_different_batches_accumulate(tmp_path: Path) -> None:
    assert_two_different_batches_accumulate(
        make_sink(tmp_path, "acc"), lambda: rows_of(tmp_path, "acc")
    )


def test_in_batch_duplicate_key_keeps_last(tmp_path: Path) -> None:
    assert_in_batch_duplicate_key_keeps_last(
        make_sink(tmp_path, "dup"), lambda: rows_of(tmp_path, "dup")
    )


def test_composite_merge_key_upsert(tmp_path: Path) -> None:
    assert_composite_merge_key_upsert(
        make_sink(tmp_path, "comp", ["a", "b"]), lambda: rows_of(tmp_path, "comp")
    )


def test_append_mode_does_not_append_the_same_unit_twice(tmp_path: Path) -> None:
    sink = make_sink(tmp_path, "events", merge_key=[])
    first = pa.RecordBatch.from_pylist([{"id": 1, "v": "a"}, {"id": 1, "v": "a"}])
    second = pa.RecordBatch.from_pylist([{"id": 2, "v": "b"}])

    sink.write(make_unit("u1"), first)
    sink.write(make_unit("u1"), first)  # a rerun of the same unit
    sink.write(make_unit("u2"), second)

    assert sorted(int(str(row["id"])) for row in rows_of(tmp_path, "events")) == [1, 1, 2]
    summaries = [snapshot.summary for snapshot in load(tmp_path, "events").snapshots()]
    assert [summary.get(UNIT_ID_PROPERTY) for summary in summaries] == [
        make_unit("u1").unit_id,
        make_unit("u2").unit_id,
    ]


def test_csv_shaped_types_and_new_columns(tmp_path: Path) -> None:
    sink = make_sink(tmp_path, "types")
    schema = pa.schema(
        [
            ("id", pa.int64()),
            ("day", pa.date32()),
            ("at", pa.timestamp("s")),
            ("zip", pa.string()),
            ("empty", pa.null()),
        ]
    )
    record = {
        "id": 1,
        "day": datetime.date(2026, 11, 1),
        "at": datetime.datetime(2026, 11, 1, 9, 30),  # noqa: DTZ001
        "zip": "02139",
        "empty": None,
    }
    sink.write(make_unit("u1"), pa.RecordBatch.from_pylist([record], schema=schema))
    assert rows_of(tmp_path, "types") == [record]

    # A later file brings a column the table does not have yet: it is added.
    sink.write(make_unit("u2"), pa.RecordBatch.from_pylist([{"id": 2, "zip": "1", "note": "new"}]))
    stored = sorted(rows_of(tmp_path, "types"), key=lambda row: int(str(row["id"])))
    assert stored[0]["note"] is None and stored[1]["note"] == "new"
    assert stored[1]["day"] is None


def test_a_type_conflict_is_a_clear_fatal_error(tmp_path: Path) -> None:
    sink = make_sink(tmp_path, "conflict")
    sink.write(make_unit("u1"), pa.RecordBatch.from_pylist([{"id": 1, "amount": 1.5}]))

    with pytest.raises(FatalError, match="iceberg write to lake.conflict failed"):
        sink.write(make_unit("u2"), pa.RecordBatch.from_pylist([{"id": 2, "amount": "n/a"}]))

    assert rows_of(tmp_path, "conflict") == [{"id": 1, "amount": 1.5}]


def test_commit_conflicts_are_retryable(tmp_path: Path) -> None:
    class CommitFailedException(Exception):
        pass

    def broken_catalog() -> Any:
        raise CommitFailedException("another writer committed first")

    spec = IcebergSinkSpec(type="iceberg", catalog={"type": "sql"}, table="lake.t")
    with pytest.raises(RetryableError, match="CommitFailedException"):
        IcebergSink(spec, load_catalog=broken_catalog).write(
            make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}])
        )


def test_pipeline_csv_to_iceberg_resumes_and_reruns_cleanly(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.csv").write_text("id,zip,amount\n1,02139,10\n2,94105,20\n", "utf-8")
    (tmp_path / "in" / "b.csv").write_text("id,zip,amount\n2,94105,25\n3,01234,30\n", "utf-8")
    spec = PipelineSpec.model_validate(
        {
            "name": "orders",
            "state_dir": str(tmp_path / ".arsenal"),
            "source": {"type": "file", "path": str(tmp_path / "in" / "*.csv")},
            "sink": {
                "type": "iceberg",
                "catalog": catalog_properties(tmp_path),
                "table": "lake.orders",
                "merge_key": ["id"],
            },
        }
    )
    assert isinstance(build_sink(spec.sink), IcebergSink)

    first = run_pipeline(spec)
    again = run_pipeline(spec)

    assert (first.written, again.written, again.skipped) == (2, 0, 2)
    stored = sorted(rows_of(tmp_path, "orders"), key=lambda row: int(str(row["id"])))
    assert stored == [
        {"id": 1, "zip": "02139", "amount": 10},
        {"id": 2, "zip": "94105", "amount": 25},
        {"id": 3, "zip": "01234", "amount": 30},
    ]


def test_spec_validation() -> None:
    with pytest.raises(ValueError, match="namespace.table"):
        IcebergSinkSpec(type="iceberg", catalog={}, table="orders")
    raw = {
        "name": "p",
        "source": {"type": "file", "path": "./in/*.csv", "detect_changes": True},
        "sink": {"type": "iceberg", "catalog": {"type": "sql"}, "table": "lake.t"},
    }
    with pytest.raises(ValueError, match="would then hold the old and the new rows"):
        PipelineSpec.model_validate(raw)
    raw["sink"] = {**raw["sink"], "merge_key": ["id"]}  # type: ignore[dict-item]
    assert PipelineSpec.model_validate(raw).sink.type == "iceberg"
