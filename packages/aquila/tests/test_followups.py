"""Multiple writers, deleted specs, the v2 migration, and opt-in remote schemas."""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from aquila import service
from aquila.model import ScanResult
from aquila.remote import iceberg_schemas, remote_parquet_schema
from aquila.store import _V1, CatalogStore  # pyright: ignore[reportPrivateUsage]


def write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def pipeline(name: str, sink: str) -> dict[str, Any]:
    return {
        "name": name,
        "source": {"type": "file", "path": f"./{name}.csv"},
        "sink": {"type": "parquet", "path": sink},
    }


def test_two_specs_writing_one_location_are_flagged(tmp_path: Path) -> None:
    write_yaml(tmp_path / "a.yaml", pipeline("orders", "./lake/orders"))
    write_yaml(tmp_path / "b.yaml", pipeline("orders_copy", "./lake/orders/"))
    store = CatalogStore(tmp_path / "cat.db")
    service.scan(store, [tmp_path], root=tmp_path)

    warnings = service.multiple_writer_warnings(store)

    assert warnings == ["orders is written by 2 specs: pugio:orders, pugio:orders_copy"]
    orders = store.resolve("orders")
    assert orders is not None and warnings[0] in service.show(store, orders)["warnings"]


def test_deleting_a_spec_removes_its_edges_and_contract(lakehouse: Path, tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "cat.db")
    service.scan(store, [lakehouse], root=lakehouse)
    before = store.counts()
    (lakehouse / "clean.yaml").unlink()
    (lakehouse / "collect.yaml").unlink()

    report = service.scan(store, [lakehouse], root=lakehouse)

    assert (report.removed_edges, report.removed_contracts) == (2, 1)
    assert store.counts()["edges"] == before["edges"] - 2
    assert store.counts()["contracts"] == 0
    assert store.counts()["datasets"] == before["datasets"]  # datasets and history are kept


def test_scanning_a_single_file_never_drops_other_specs(lakehouse: Path, tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "cat.db")
    service.scan(store, [lakehouse], root=lakehouse)

    report = service.scan(store, [lakehouse / "clean.yaml"], root=lakehouse)

    assert report.removed_edges == 0 and store.counts()["edges"] == 4


def test_a_v1_catalog_migrates_to_v2_and_keeps_its_rows(lakehouse: Path, tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    for statement in _V1:
        con.execute(statement)
    con.execute(
        "INSERT INTO datasets (dataset_id, name, uri, format, created_at, updated_at) "
        "VALUES ('x', 'legacy', 'file:///legacy', 'parquet', 't', 't')"
    )
    con.execute("PRAGMA user_version=1")
    con.commit()
    con.close()

    store = CatalogStore(db)
    service.scan(store, [lakehouse], root=lakehouse)

    assert store.resolve("legacy") is not None and store.counts()["edges"] == 4
    store.close()
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 2


def test_remote_parquet_footer_through_an_injected_filesystem(tmp_path: Path) -> None:
    prefix = tmp_path / "bucket" / "orders"
    prefix.mkdir(parents=True)
    pq.write_table(pa.table({"id": [1]}), prefix / "part-0.parquet")  # pyright: ignore[reportUnknownMemberType]
    pq.write_table(pa.table({"id": [1], "email": ["a"]}), prefix / "part-1.parquet")  # pyright: ignore[reportUnknownMemberType]
    result = ScanResult()

    local: Any = importlib.import_module("pyarrow.fs").LocalFileSystem()

    def opener(_uri: str) -> tuple[Any, str]:
        return local, str(prefix)

    remote_parquet_schema("s3://bucket/orders", result, open_filesystem=opener)

    [observation] = result.schemas
    assert observation.origin == "remote-footer" and '"email"' in observation.snapshot


def test_remote_failures_are_skipped_without_provider_text() -> None:
    def denied(_uri: str) -> tuple[Any, str]:
        raise PermissionError("https://bucket.s3/presigned?X-Amz-Signature=sekrit")

    result = ScanResult()
    remote_parquet_schema("s3://bucket/orders", result, open_filesystem=denied)

    assert result.schemas == [] and result.skipped == [
        ("s3://bucket/orders", "remote parquet unreadable (PermissionError)")
    ]


def test_remote_reads_are_opt_in(tmp_path: Path) -> None:
    write_yaml(tmp_path / "p.yaml", pipeline("orders", "s3://bucket/orders"))

    result = service.collect([tmp_path], root=tmp_path)

    assert result.schemas == [] and result.skipped == []


@dataclass
class FakeSnapshot:
    schema_id: int | None
    timestamp_ms: int


@dataclass
class FakeSchema:
    schema_id: int
    arrow: pa.Schema

    def as_arrow(self) -> pa.Schema:
        return self.arrow


class FakeTable:
    def __init__(self) -> None:
        v0 = FakeSchema(0, pa.schema([pa.field("id", pa.int64(), nullable=False)]))
        v1 = FakeSchema(
            1, pa.schema([pa.field("id", pa.int64(), nullable=False), ("day", pa.date32())])
        )
        self._schemas = {0: v0, 1: v1, 2: FakeSchema(2, pa.schema([("unused", pa.string())]))}
        self._snapshots = [FakeSnapshot(1, 2_000_000_000_000), FakeSnapshot(0, 1_700_000_000_000)]

    def snapshots(self) -> list[FakeSnapshot]:
        return self._snapshots

    def schemas(self) -> dict[int, FakeSchema]:
        return self._schemas

    def schema(self) -> FakeSchema:
        return self._schemas[1]


def test_iceberg_records_every_schema_a_snapshot_used(tmp_path: Path) -> None:
    loaded: list[str] = []

    def load(identifier: str) -> FakeTable:
        loaded.append(identifier)
        return FakeTable()

    result = ScanResult()
    iceberg_schemas("iceberg://analytics.orders_daily", result, load_table=load)

    assert loaded == ["analytics.orders_daily"]
    assert [s.observed_at[:4] for s in result.schemas] == ["2023", "2033"]
    assert '"day"' in result.schemas[1].snapshot and "unused" not in "".join(
        s.snapshot for s in result.schemas
    )

    store = CatalogStore(tmp_path / "cat.db")
    write_yaml(
        tmp_path / "t.yaml",
        {"name": "daily", "input": "./in", "output": "iceberg://analytics.orders_daily"},
    )
    service.scan(store, [tmp_path], root=tmp_path, iceberg=load)
    daily = store.resolve("daily")
    assert daily is not None
    assert service.history(store, daily)[1]["diff"]["added"] == ["day"]


def test_iceberg_failures_are_skipped() -> None:
    def broken(_identifier: str) -> Any:
        raise ConnectionError("https://catalog/v1?token=sekrit")

    result = ScanResult()
    iceberg_schemas("iceberg://a.b", result, load_table=broken)

    assert result.skipped == [("iceberg://a.b", "iceberg table unreadable (ConnectionError)")]


def test_cli_iceberg_errors_are_one_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from aquila.cli import app

    monkeypatch.delenv("NO_SUCH_TOKEN", raising=False)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(tmp_path),
            "--db",
            str(tmp_path / "c.db"),
            "--iceberg-uri",
            "http://catalog",
            "--iceberg-token-env",
            "NO_SUCH_TOKEN",
        ],
    )

    expected = "NO_SUCH_TOKEN" if importlib.util.find_spec("pyiceberg") else "needs pyiceberg"
    assert result.exit_code == 1 and expected in result.output
    assert not (tmp_path / "c.db").exists()
