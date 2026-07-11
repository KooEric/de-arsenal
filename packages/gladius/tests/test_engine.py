"""DuckDB 실행 엔진 — 실물 in→out 검증, 원자적 출력 (M3 Task 3.4)."""

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
