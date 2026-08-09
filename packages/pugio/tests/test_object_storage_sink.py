import duckdb
import pyarrow as pa
import pytest

from arsenal_core.state import UnitSpec
from pugio.sinks.object_storage import ObjectStorageParquetSink


class FakeConnection:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, statement: str) -> None:
        self.sql.append(statement)

    def register(self, name: str, value: object) -> None:
        self.sql.append(f"REGISTER {name}")

    def close(self) -> None:
        self.sql.append("CLOSE")


def test_object_storage_sink_uses_deterministic_unit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeConnection()
    monkeypatch.setattr(duckdb, "connect", lambda: fake)
    sink = ObjectStorageParquetSink("s3://bucket/prefix/")
    unit = UnitSpec.create(pipeline="p", source="s", unit_key="u", payload={})

    sink.write(unit, pa.RecordBatch.from_pylist([{"id": 1}]))

    assert "INSTALL httpfs" in fake.sql
    assert "LOAD httpfs" in fake.sql
    copy = next(statement for statement in fake.sql if statement.startswith("COPY"))
    assert f"s3://bucket/prefix/{unit.unit_id}.parquet" in copy
