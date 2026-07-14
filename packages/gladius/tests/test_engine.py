"""DuckDB 실행 엔진 — 실물 in→out 검증, 원자적 출력 (M3 Task 3.4)."""

import os
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from arsenal_core.errors import FatalError
from gladius.engine import run_transform
from gladius.spec import TransformSpec


def make_spec(input: Path, output: Path, steps: list[dict[str, Any]]) -> TransformSpec:
    return TransformSpec.model_validate(
        {"name": "t", "input": input, "output": output, "steps": steps}
    )


def test_transform_writes_parquet(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2, 3], "state": ["open", "closed", "open"]}),
        tmp_path / "in" / "a.parquet",
    )
    spec = make_spec(
        input=tmp_path / "in",
        output=tmp_path / "out",
        steps=[{"filter": "state = 'open'"}],
    )
    out = run_transform(spec)
    row = duckdb.sql(f"SELECT count(*) FROM read_parquet('{out}/*.parquet')").fetchone()
    assert row is not None
    assert row[0] == 2


def test_output_write_is_atomic(tmp_path: Path) -> None:
    # 임시 디렉터리에 쓰고 os.replace — 실패한 실행이 부분 출력물을 남기지 않는다
    (tmp_path / "in").mkdir()
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2]}),
        tmp_path / "in" / "a.parquet",
    )
    good_spec = make_spec(
        input=tmp_path / "in", output=tmp_path / "out", steps=[{"select": ["id"]}]
    )
    out = run_transform(good_spec)
    before = sorted(p.name for p in out.iterdir())

    bad_spec = make_spec(
        input=tmp_path / "in",
        output=tmp_path / "out",
        steps=[{"filter": "no_such_column = 1"}],
    )
    with pytest.raises(FatalError):
        run_transform(bad_spec)

    after = sorted(p.name for p in Path(tmp_path / "out").iterdir())
    assert after == before  # 실패한 실행이 이전 성공 출력을 훼손하지 않는다


def test_run_transform_output_path_with_single_quote(tmp_path: Path) -> None:
    # CRITICAL 리뷰 발견 회귀 테스트: output 경로에 작은따옴표가 있으면 임시
    # 파일 경로를 COPY ... TO '{tmp}' 에 보간할 때 SQL 리터럴이 깨질 수 있다 —
    # 이스케이프되어 정상적으로 실행되어야 한다.
    (tmp_path / "in").mkdir()
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2, 3]}),
        tmp_path / "in" / "a.parquet",
    )
    out_dir = tmp_path / "o'ut"
    spec = make_spec(input=tmp_path / "in", output=out_dir, steps=[{"select": ["id"]}])
    out = run_transform(spec)
    assert out == out_dir
    # 파라미터 바인딩으로 조회 — 이 테스트 자체는 인젝션에 안전한 경로로 검증한다.
    row = duckdb.execute("SELECT count(*) FROM read_parquet(?)", [f"{out}/*.parquet"]).fetchone()
    assert row is not None
    assert row[0] == 3


def test_run_transform_survives_cpu_count_returning_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MINOR 리뷰 발견 회귀 테스트: os.cpu_count()가 None을 반환할 수 있는 환경에서
    # PRAGMA threads=None 이 되어 실패하면 안 된다 — `or 1`로 가드되어야 한다.
    (tmp_path / "in").mkdir()
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2]}),
        tmp_path / "in" / "a.parquet",
    )
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    spec = make_spec(input=tmp_path / "in", output=tmp_path / "out", steps=[{"select": ["id"]}])
    out = run_transform(spec)
    row = duckdb.sql(f"SELECT count(*) FROM read_parquet('{out}/*.parquet')").fetchone()
    assert row is not None
    assert row[0] == 2
