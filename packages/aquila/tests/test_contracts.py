"""Contract checks judge what a Parquet sink stores, not artefacts of the format.

Parquet fields are nullable in the footer even when a column holds no nulls, and
Parquet has no second-precision timestamp, so pyarrow stores ``timestamp[s]`` as
``timestamp[ms]``. Neither is a contract violation; real mismatches still are.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from aquila import service
from aquila.model import ContractObservation, ScanResult, SchemaObservation, schema_snapshot
from aquila.remote import remote_parquet_schema
from aquila.store import CatalogStore

T1 = datetime(2026, 1, 1)
T2 = datetime(2026, 1, 2)


def project(
    tmp_path: Path,
    table: pa.Table,
    columns: list[dict[str, Any]],
    *,
    sink_type: str = "parquet",
) -> Path:
    root = tmp_path / "project"
    sink = root / "lake" / "events"
    sink.mkdir(parents=True)
    pq.write_table(table, sink / "part-0.parquet")  # pyright: ignore[reportUnknownMemberType]
    spec = {
        "name": "events",
        "state_dir": ".arsenal",
        "source": {"type": "file", "path": "./raw/*.jsonl"},
        "sink": {"type": sink_type, "path": "./lake/events"},
        "contract": {"columns": columns},
    }
    (root / "events.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return root


def findings(tmp_path: Path, root: Path) -> list[str]:
    store = CatalogStore(tmp_path / "aquila.db")
    try:
        service.scan(store, [root], root=root)
        dataset = store.resolve("events")
        assert dataset is not None
        return service.contract_findings(store, dataset)
    finally:
        store.close()


def events(ids: list[str | None], ts: list[datetime | None], unit: str = "s") -> pa.Table:
    return pa.table({"id": pa.array(ids, pa.string()), "ts": pa.array(ts, pa.timestamp(unit))})


CONTRACT = [
    {"name": "id", "type": "string", "nullable": False},
    {"name": "ts", "type": "timestamp[s]", "nullable": False},
]


def test_a_parquet_sink_that_satisfies_its_contract_has_no_findings(tmp_path: Path) -> None:
    root = project(tmp_path, events(["a", "b"], [T1, T2]), CONTRACT)

    assert findings(tmp_path, root) == []


def test_nulls_in_a_not_null_column_are_still_reported(tmp_path: Path) -> None:
    root = project(tmp_path, events(["a", None], [T1, T2]), CONTRACT)

    assert findings(tmp_path, root) == ["id: 1 null value(s) in data, NOT NULL in contract"]


def test_a_different_base_type_is_still_reported(tmp_path: Path) -> None:
    contract = [
        {"name": "id", "type": "int64", "nullable": False},
        {"name": "ts", "type": "date32[day]"},
    ]
    root = project(tmp_path, events(["a"], [T1]), contract)

    assert findings(tmp_path, root) == [
        "id: type string != contract int64",
        "ts: type timestamp[ms] != contract date32[day]",
    ]


def test_only_the_seconds_to_millis_coercion_is_forgiven(tmp_path: Path) -> None:
    contract = [{"name": "id", "type": "string"}, {"name": "ts", "type": "timestamp[s]"}]
    root = project(tmp_path, events(["a"], [T1], unit="us"), contract)

    assert findings(tmp_path, root) == ["ts: type timestamp[us] != contract timestamp[s]"]


def test_timezones_must_still_match(tmp_path: Path) -> None:
    table = pa.table({"ts": pa.array([T1], pa.timestamp("s", tz="UTC"))})
    contract = [{"name": "ts", "type": "timestamp[s, tz=Asia/Seoul]"}]
    root = project(tmp_path, table, contract)

    assert findings(tmp_path, root) == [
        "ts: type timestamp[ms, tz=UTC] != contract timestamp[s, tz=Asia/Seoul]"
    ]
    ok = project(tmp_path / "ok", table, [{"name": "ts", "type": "timestamp[s, tz=UTC]"}])
    assert findings(tmp_path / "ok", ok) == []


def test_rescans_track_nulls_without_new_schema_versions(tmp_path: Path) -> None:
    root = project(tmp_path, events(["a"], [T1]), CONTRACT)
    assert findings(tmp_path, root) == []

    part = root / "lake" / "events" / "part-0.parquet"
    pq.write_table(events([None, None], [T1, T2]), part)  # pyright: ignore[reportUnknownMemberType]
    store = CatalogStore(tmp_path / "aquila.db")
    report = service.scan(store, [root], root=root)
    store.close()

    assert report.new_schema_versions == []
    assert findings(tmp_path, root) == ["id: 2 null value(s) in data, NOT NULL in contract"]


def test_missing_null_statistics_raise_no_finding_for_parquet(tmp_path: Path) -> None:
    root = project(tmp_path, events(["a"], [T1]), CONTRACT)
    part = root / "lake" / "events" / "part-0.parquet"
    pq.write_table(events([None], [T1]), part, write_statistics=False)  # pyright: ignore[reportUnknownMemberType]

    assert findings(tmp_path, root) == []


def test_remote_footers_record_null_counts(tmp_path: Path) -> None:
    prefix = tmp_path / "bucket" / "events"
    prefix.mkdir(parents=True)
    pq.write_table(events(["a", None], [T1, None]), prefix / "part-0.parquet")  # pyright: ignore[reportUnknownMemberType]
    local: Any = importlib.import_module("pyarrow.fs").LocalFileSystem()
    result = ScanResult()

    remote_parquet_schema(
        "s3://bucket/events", result, open_filesystem=lambda _u: (local, str(prefix))
    )

    [nulls] = result.null_counts
    assert nulls.uri == "s3://bucket/events" and nulls.counts == {"id": 1, "ts": 1}


def test_formats_with_a_real_nullable_flag_still_trust_it(tmp_path: Path) -> None:
    uri = "iceberg://analytics.events"
    result = ScanResult()
    result.dataset(uri, "iceberg")
    snapshot = schema_snapshot([{"name": "ts", "type": "timestamp[ms]", "nullable": True}])
    result.schemas.append(SchemaObservation(uri, snapshot, "2026-01-01T00:00:00+00:00", "iceberg"))
    contract = {"columns": [{"name": "ts", "type": "timestamp[s]", "nullable": False}]}
    result.contracts.append(ContractObservation(uri, "events.yaml", contract))
    store = CatalogStore(tmp_path / "aquila.db")
    store.apply(result)
    dataset = store.resolve(uri)
    assert dataset is not None

    assert service.contract_findings(store, dataset) == [
        "ts: type timestamp[ms] != contract timestamp[s]"
    ]
    contract["columns"][0]["type"] = "timestamp[ms]"
    result.contracts[0] = ContractObservation(uri, "events.yaml", contract)
    store.apply(result)
    assert service.contract_findings(store, dataset) == [
        "ts: nullable in data, NOT NULL in contract"
    ]
    store.close()
