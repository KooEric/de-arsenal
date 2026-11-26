"""Write small datasets for the data-health tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

ROWS: list[dict[str, Any]] = [
    {"id": 1, "v": "a", "note": None, "ts": datetime(2026, 11, 1, tzinfo=UTC)},
    {"id": 2, "v": "b", "note": None, "ts": datetime(2026, 11, 10, tzinfo=UTC)},
    {"id": 3, "v": None, "note": None, "ts": datetime(2026, 11, 20, tzinfo=UTC)},
    {"id": 4, "v": "d", "note": "x", "ts": datetime(2026, 11, 24, tzinfo=UTC)},
]
DUPLICATE: dict[str, Any] = {"id": 1, "v": "again", "note": None, "ts": ROWS[0]["ts"]}


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)  # pyright: ignore[reportUnknownMemberType]
    return path


def write_duckdb(path: Path, table: str, rows: int, *, duplicate_every: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = f"CASE WHEN i % {duplicate_every} = 0 THEN 0 ELSE i END" if duplicate_every else "i"
    with duckdb.connect(str(path)) as db:
        db.execute(
            f"CREATE TABLE {table} AS SELECT {key} AS id, 'v' || i AS v, "
            f"TIMESTAMPTZ '2026-11-01 00:00:00+00' + to_seconds(i) AS ts FROM range({rows}) t(i)"
        )
    return path
