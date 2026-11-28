"""Write an `events` Parquet file for tests."""

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("event_time", pa.timestamp("us")),
        ("source", pa.string()),
        ("service", pa.string()),
        ("action", pa.string()),
        ("actor_type", pa.string()),
        ("actor_id", pa.string()),
        ("src_ip", pa.string()),
        ("region", pa.string()),
        ("outcome", pa.string()),
        ("mfa_used", pa.bool_()),
        ("resource", pa.string()),
        ("raw", pa.string()),
    ]
)


def write_events(path: Path, rows: list[dict[str, Any]]) -> None:
    full = [{**dict.fromkeys(SCHEMA.names), **row} for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(full, schema=SCHEMA), path)  # pyright: ignore[reportUnknownMemberType]
