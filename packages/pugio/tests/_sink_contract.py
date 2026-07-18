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
