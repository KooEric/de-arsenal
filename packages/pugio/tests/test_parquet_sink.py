from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec
from pugio.sinks.parquet import ParquetSink


def batch(ids: list[int]) -> pa.RecordBatch:
    return pa.RecordBatch.from_pylist([{"id": i} for i in ids])


def unit(key: str) -> UnitSpec:
    return UnitSpec.create(pipeline="p", source="s", unit_key=key, payload={})


def test_writes_file_named_by_unit_id(tmp_path: Path) -> None:
    sink = ParquetSink(tmp_path / "out")
    u = unit("offset=0")
    sink.write(u, batch([1, 2]))
    assert (tmp_path / "out" / f"{u.unit_id}.parquet").exists()


def test_write_twice_same_unit_is_idempotent(tmp_path: Path) -> None:
    sink = ParquetSink(tmp_path / "out")
    u = unit("offset=0")
    sink.write(u, batch([1, 2]))
    sink.write(u, batch([1, 2]))  # 재실행 시뮬레이션
    files = list((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 1
    # pyarrow.parquet has no type stubs; read_table()'s return type is Unknown.
    table = pq.read_table(files[0])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert table.num_rows == 2  # pyright: ignore[reportUnknownMemberType]  # 중복 없음


def test_no_partial_file_visible(tmp_path: Path) -> None:
    """tmp에 쓰고 atomic rename — .tmp 잔여물이 결과로 보이면 안 된다."""
    sink = ParquetSink(tmp_path / "out")
    sink.write(unit("offset=0"), batch([1]))
    assert not list((tmp_path / "out").glob("*.tmp"))
