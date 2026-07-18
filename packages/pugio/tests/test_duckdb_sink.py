from pathlib import Path

import duckdb
import pyarrow as pa
import pytest
from _sink_contract import (
    assert_merge_updates_changed_rows,
    assert_two_different_batches_accumulate,
    assert_write_twice_same_unit_is_idempotent,
    make_unit,
)

from arsenal_core.errors import RetryableError
from arsenal_core.spec.models import DuckDBSinkSpec
from pugio.sinks.duckdb import DuckDBSink


def make_sink(tmp_path: Path, *, table: str = "t") -> tuple[DuckDBSink, Path]:
    db_path = tmp_path / "sink.duckdb"
    spec = DuckDBSinkSpec(type="duckdb", path=str(db_path), table=table, merge_key=["id"])
    return DuckDBSink(spec), db_path


def readback(db_path: Path, table: str = "t") -> list[dict[str, object]]:
    con = duckdb.connect(str(db_path))
    try:
        arrow_table = con.execute(f'SELECT * FROM "{table}"').to_arrow_table()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return arrow_table.to_pylist()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    finally:
        con.close()


def test_write_twice_same_unit_row_count_unchanged(tmp_path: Path) -> None:
    sink, db_path = make_sink(tmp_path)
    assert_write_twice_same_unit_is_idempotent(sink, lambda: readback(db_path))


def test_merge_updates_changed_rows(tmp_path: Path) -> None:
    sink, db_path = make_sink(tmp_path)
    assert_merge_updates_changed_rows(sink, lambda: readback(db_path))


def test_write_two_different_batches_accumulates(tmp_path: Path) -> None:
    sink, db_path = make_sink(tmp_path)
    assert_two_different_batches_accumulate(sink, lambda: readback(db_path))


def test_write_failure_raises_retryable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DELETE/INSERT 트랜잭션 도중 예외가 나면 RetryableError로 감싸 전파하고 롤백한다."""
    sink, _ = make_sink(tmp_path)
    u = make_unit("boom")
    batch = pa.RecordBatch.from_pylist([{"id": 1, "v": "a"}])
    sink.write(u, batch)  # 테이블 생성

    real_connect = duckdb.connect

    class BoomConnection:
        def __init__(self, real: object) -> None:
            self._real = real

        def register(self, *a: object, **kw: object) -> None:
            return self._real.register(*a, **kw)  # type: ignore[attr-defined]

        def execute(self, sql: str, *a: object, **kw: object) -> object:
            if sql.strip().upper().startswith("INSERT"):
                raise RuntimeError("simulated failure")
            return self._real.execute(sql, *a, **kw)  # type: ignore[attr-defined]

        def close(self) -> None:
            return self._real.close()  # type: ignore[attr-defined]

    def fake_connect(path: str) -> object:
        return BoomConnection(real_connect(path))

    monkeypatch.setattr(duckdb, "connect", fake_connect)
    with pytest.raises(RetryableError):
        sink.write(u, batch)
