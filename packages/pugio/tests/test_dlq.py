from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec
from pugio.dlq import dlq_dir, read_reason, remove_dlq, write_dlq
from pugio.validate.gate import Violation


def make_unit() -> UnitSpec:
    return UnitSpec.create(pipeline="p", source="s", unit_key="offset=0", payload={"offset": 0})


def test_reason_json_round_trips_non_ascii(tmp_path: Path) -> None:
    """사유 JSON은 ensure_ascii=False로 쓰므로 UTF-8로 고정해야 왕복한다 —
    플랫폼 기본 인코딩(Windows cp1252)에 맡기면 쓰기/읽기가 깨진다."""
    unit = make_unit()
    batch = pa.RecordBatch.from_pylist([{"금액": 1}])
    violations = [Violation("not_null", "금액", 1)]

    parquet_path = write_dlq(tmp_path, "p", unit, batch, violations)

    json_path = parquet_path.with_suffix(".json")
    assert "금액".encode() in json_path.read_bytes()  # 디스크에 UTF-8로 기록
    reason = read_reason(json_path)
    violation = cast(list[dict[str, object]], reason["violations"])[0]
    assert violation["field"] == "금액"


def test_write_dlq_creates_parquet_and_reason_json(tmp_path: Path) -> None:
    unit = make_unit()
    batch = pa.RecordBatch.from_pylist([{"v": 1}, {"v": None}])
    violations = [Violation("not_null", "v", 1)]

    parquet_path = write_dlq(tmp_path, "p", unit, batch, violations)

    assert parquet_path.exists()
    json_path = parquet_path.with_suffix(".json")
    assert json_path.exists()

    table = pq.read_table(parquet_path)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert table.num_rows == 2  # pyright: ignore[reportUnknownMemberType]

    reason = read_reason(json_path)
    assert reason["unit_id"] == unit.unit_id
    assert reason["unit_key"] == "offset=0"
    assert reason["violations"] == [{"rule": "not_null", "field": "v", "count": 1}]
    assert "quarantined_at" in reason


def test_remove_dlq_deletes_both(tmp_path: Path) -> None:
    unit = make_unit()
    batch = pa.RecordBatch.from_pylist([{"v": 1}])
    write_dlq(tmp_path, "p", unit, batch, [Violation("not_null", "v", 0)])

    remove_dlq(tmp_path, "p", unit.unit_id)

    d = dlq_dir(tmp_path, "p")
    assert not (d / f"{unit.unit_id}.parquet").exists()
    assert not (d / f"{unit.unit_id}.json").exists()


def test_remove_dlq_is_safe_when_missing(tmp_path: Path) -> None:
    """존재하지 않는 unit_id를 지워도 에러 없이 no-op (retry 재호출 등에 대비)."""
    remove_dlq(tmp_path, "p", "does-not-exist")
