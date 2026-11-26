"""A three-stage lakehouse: Pugio collect → Gladius clean → Ballista daily, plus a Hasta stream."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from arsenal_core.state import StateStore

ID = '{"name": "id", "type": "int64", "nullable": true}'
AMOUNT = '{"name": "amount", "type": "double", "nullable": true}'
EMAIL = '{"name": "email", "type": "string", "nullable": true}'
V1 = f"[{ID}, {AMOUNT}]"
V2 = f"[{ID}, {AMOUNT}, {EMAIL}]"


def write_yaml(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def lakehouse(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    write_yaml(
        root / "arsenal.yaml",
        {
            "name": "lakehouse",
            "pipelines": ["collect.yaml"],
            "transforms": ["clean.yaml"],
            "distributed": [
                {"transform": "daily.yaml", "profile": "spark.yaml", "input_version": "auto"}
            ],
        },
    )
    write_yaml(
        root / "collect.yaml",
        {
            "name": "orders",
            "state_dir": ".arsenal",
            "source": {"type": "file", "path": "./raw/*.csv"},
            "sink": {"type": "parquet", "path": "./lake/raw/orders"},
            "contract": {
                "columns": [
                    {"name": "id", "type": "int64", "nullable": False},
                    {"name": "amount", "type": "double"},
                ],
                "allow_extra": True,
            },
        },
    )
    write_yaml(
        root / "clean.yaml",
        {
            "name": "orders_clean",
            "input": "./lake/raw/orders/*.parquet",
            "steps": [{"filter": "amount > 0"}],
            "output": "./lake/clean/orders",
        },
    )
    write_yaml(
        root / "daily.yaml",
        {
            "name": "orders_daily",
            "input": "./lake/clean/orders/*.parquet",
            "steps": [{"select": ["id", "amount"]}],
            "output": "iceberg://analytics.orders_daily",
        },
    )
    write_yaml(
        root / "streams" / "events.yaml",
        {
            "name": "events",
            "state_dir": ".arsenal",
            "source": {
                "type": "rest_poll",
                "url": "https://api.example.test/events?api_key=sk-live-777",
                "cursor": {
                    "request_param": "after",
                    "response_path": "next",
                    "event_id_path": "id",
                },
            },
            "stream": {"name": "events", "poll_interval_seconds": 1, "batch": {}, "checkpoint": {}},
            "sink": {"type": "parquet", "path": "./lake/raw/events"},
        },
    )
    write_yaml(root / "spark.yaml", {"executor": "spark_kubernetes"})  # not a spec: ignored

    store = StateStore(root / ".arsenal" / "orders.db")
    store.snapshot_schema("orders", V1)
    store.snapshot_schema("orders", V2)
    store.close()

    raw = root / "lake" / "raw" / "orders"
    raw.mkdir(parents=True)
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]  # V2's shape, one null id
        pa.table(
            {
                "id": pa.array([1, None], pa.int64()),
                "amount": [1.5, 2.5],
                "email": pa.array(["a@x", None], pa.string()),
            }
        ),
        raw / "part-0.parquet",
    )

    clean = root / "lake" / "clean" / "orders"
    clean.mkdir(parents=True)
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2], "amount": [1.5, 2.5]}), clean / "part-0.parquet"
    )
    return root
