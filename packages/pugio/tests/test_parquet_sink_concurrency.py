"""ParquetSink의 임시 파일은 writer마다 고유해야 한다.

임시 경로가 `{unit_id}.parquet.tmp`로 결정적이면, 같은 유닛을 동시에 쓰는 두
writer가 **같은 파일에 섞어 쓴 뒤** 각자 os.replace 한다. rename은 원자적이지만
옮겨지는 파일이 이미 깨져 있으므로, 원자성 보장이 이 경우를 덮지 못한다.
"""

import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec
from pugio.sinks.parquet import ParquetSink


def _unit() -> UnitSpec:
    return UnitSpec.create(pipeline="p", source="s", unit_key="k", payload={})


def test_tmp_path_is_unique_per_writer(tmp_path: Path) -> None:
    """두 sink 인스턴스가 같은 유닛에 대해 서로 다른 임시 경로를 골라야 한다."""
    unit = _unit()
    # 내부 메서드를 직접 부른다 — 임시 경로 선택은 관측 가능한 결과가 아니라
    # 동시성 안전의 전제이므로, 아래 동시 쓰기 테스트와 별개로 직접 고정한다.
    a = ParquetSink(tmp_path)._tmp_path(unit)  # pyright: ignore[reportPrivateUsage]
    b = ParquetSink(tmp_path)._tmp_path(unit)  # pyright: ignore[reportPrivateUsage]
    assert a != b
    assert a.name.startswith(unit.unit_id) and a.name.endswith(".tmp")


def test_concurrent_writes_of_same_unit_leave_a_readable_file(tmp_path: Path) -> None:
    """같은 유닛을 동시에 쓰더라도 최종 파일은 온전히 읽혀야 한다."""
    unit = _unit()
    batch = pa.RecordBatch.from_pylist([{"i": n} for n in range(500)])
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            ParquetSink(tmp_path).write(unit, batch)
        except BaseException as exc:  # noqa: BLE001 - 스레드 예외를 본 스레드로 옮긴다
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    table = pq.read_table(tmp_path / f"{unit.unit_id}.parquet")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert table.num_rows == 500  # pyright: ignore[reportUnknownMemberType]
    # 임시 파일이 결과 디렉터리에 남지 않는다
    assert list(tmp_path.glob("*.tmp")) == []
