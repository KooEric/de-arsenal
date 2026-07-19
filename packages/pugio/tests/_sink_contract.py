"""DuckDB/Postgres 두 DB 싱크가 공유하는 멱등 계약 검증 헬퍼 (M2-F Task F4).

두 싱크 모두 "temp table → merge_key 기준 delete+insert(또는 upsert)"로 멱등성을
구현한다 — 이 파일은 그 계약을 싱크 생성/write/readback 콜백으로 매개변수화해
중복 없이 재사용한다. parquet 싱크는 파일 기반이라 계약이 달라 대상에서 뺐다
(sinks/base.py Sink 프로토콜 docstring 참조).

readback: 대상 테이블의 전체 행을 dict 리스트로 돌려주는 콜백 — 정렬은 호출부가 한다.
"""

from collections.abc import Callable
from typing import cast

import pyarrow as pa

from arsenal_core.state import UnitSpec
from pugio.sinks.base import Sink

ReadBack = Callable[[], list[dict[str, object]]]


def make_unit(key: str) -> UnitSpec:
    return UnitSpec.create(pipeline="contract-p", source="contract-s", unit_key=key, payload={})


def assert_write_twice_same_unit_is_idempotent(sink: Sink, readback: ReadBack) -> None:
    """같은 batch를 같은 unit으로 두 번 써도 행 수가 늘지 않는다 (재실행 시뮬레이션)."""
    u = make_unit("u1")
    batch = pa.RecordBatch.from_pylist([{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])
    sink.write(u, batch)
    sink.write(u, batch)
    rows = readback()
    assert len(rows) == 2


def assert_merge_updates_changed_rows(sink: Sink, readback: ReadBack) -> None:
    """같은 merge_key(id=1)로 값만 바뀐 batch를 다시 쓰면 최신 값으로 대체된다."""
    u = make_unit("u2")
    sink.write(u, pa.RecordBatch.from_pylist([{"id": 1, "v": "a"}]))
    sink.write(u, pa.RecordBatch.from_pylist([{"id": 1, "v": "b"}]))
    rows = readback()
    assert len(rows) == 1
    assert rows[0]["v"] == "b"


def assert_two_different_batches_accumulate(sink: Sink, readback: ReadBack) -> None:
    """서로 다른 merge_key(id)를 가진 batch는 누적된다(덮어쓰지 않음)."""
    u1 = make_unit("u3")
    u2 = make_unit("u4")
    sink.write(u1, pa.RecordBatch.from_pylist([{"id": 1, "v": "a"}]))
    sink.write(u2, pa.RecordBatch.from_pylist([{"id": 2, "v": "b"}]))
    rows = readback()
    ids = sorted(cast(int, r["id"]) for r in rows)
    assert ids == [1, 2]


def assert_in_batch_duplicate_key_keeps_last(sink: Sink, readback: ReadBack) -> None:
    """한 batch 안에 같은 merge_key(id=1)가 중복되면 merge_key당 정확히 한 행만
    남고, 그 값은 batch 내 마지막(row order 기준) 값이어야 한다 (M2-F FIX 2).
    postgres는 ON CONFLICT가 row 단위로 처리되어 자연히 last-wins가 되지만,
    duckdb는 유니크 제약이 없는 set INSERT라 별도 조치 없이는 중복 행이 그대로
    쌓인다 — 두 싱크가 동일한 최종 상태를 내는지 이 계약으로 함께 검증한다."""
    u = make_unit("u_dup")
    batch = pa.RecordBatch.from_pylist([{"id": 1, "v": "a"}, {"id": 1, "v": "b"}])
    sink.write(u, batch)
    rows = readback()
    assert len(rows) == 1
    assert rows[0]["v"] == "b"


def assert_composite_merge_key_upsert(sink: Sink, readback: ReadBack) -> None:
    """복합 merge_key(a, b) 기준으로 두 번째 write가 (1,1)은 갱신하고 (1,2)는
    새로 추가한다 — DELETE/ON CONFLICT의 다중 컬럼 매치 조건이 올바른지 검증한다
    (M2-F FIX 5). 싱크 생성 시 merge_key=["a", "b"]로 만들어야 호출할 수 있다."""
    u1 = make_unit("u_comp1")
    u2 = make_unit("u_comp2")
    sink.write(u1, pa.RecordBatch.from_pylist([{"a": 1, "b": 1, "v": "x"}]))
    sink.write(
        u2,
        pa.RecordBatch.from_pylist([{"a": 1, "b": 1, "v": "y"}, {"a": 1, "b": 2, "v": "z"}]),
    )
    rows = readback()
    assert len(rows) == 2
    by_key = {(cast(int, r["a"]), cast(int, r["b"])): r["v"] for r in rows}
    assert by_key == {(1, 1): "y", (1, 2): "z"}
