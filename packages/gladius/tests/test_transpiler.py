"""steps → SQL 트랜스파일러 스냅샷 테스트 (M3 Task 3.3)."""

from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import given
from hypothesis import strategies as st

from gladius.compile.transpiler import compile_sql
from gladius.spec import TransformSpec


def make(steps: list[dict[str, Any]], map_: dict[str, str] | None = None) -> TransformSpec:
    return TransformSpec.model_validate(
        {
            "name": "t",
            "input": "./in",
            "output": "./out",
            **({"map": map_} if map_ else {}),
            "steps": steps,
        }
    )


def test_full_chain_compiles_to_cte_pipeline() -> None:
    sql = compile_sql(
        make(
            steps=[
                {"filter": "state = 'open'"},
                {"rename": {"created_at": "opened_at"}},
                {"cast": {"number": "bigint"}},
                {"dedup": ["number"]},
                {"select": ["number", "title", "opened_at"]},
            ],
        )
    )
    assert sql == (
        "WITH s0 AS (SELECT * FROM read_parquet('./in/**/*.parquet', union_by_name=true)),\n"
        "s1 AS (SELECT * FROM s0 WHERE state = 'open'),\n"
        's2 AS (SELECT * EXCLUDE ("created_at"), "created_at" AS "opened_at" FROM s1),\n'
        's3 AS (SELECT * REPLACE (CAST("number" AS BIGINT) AS "number") FROM s2),\n'
        "s4 AS (SELECT * FROM s3 QUALIFY row_number() "
        'OVER (PARTITION BY "number" ORDER BY "number") = 1)\n'
        'SELECT "number", "title", "opened_at" FROM s4'
    )


def test_map_becomes_first_projection() -> None:
    sql = compile_sql(make(steps=[], map_={"issue_no": "number"}))
    assert 's1 AS (SELECT number AS "issue_no" FROM s0)' in sql


def test_every_generated_sql_parses_in_duckdb(tmp_path_factory: pytest.TempPathFactory) -> None:
    # 임의 step 체인 → duckdb PREPARE가 성공해야 한다 (실행은 안 함)
    data_dir = tmp_path_factory.mktemp("data")
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"number": [1], "title": ["a"], "state": ["open"], "created_at": ["2020-01-01"]}),
        data_dir / "a.parquet",
    )

    step_strategy = st.sampled_from(
        [
            {"filter": "state = 'open'"},
            {"rename": {"created_at": "opened_at"}},
            {"cast": {"number": "bigint"}},
            {"dedup": ["number"]},
            {"derive": {"is_open": "state = 'open'"}},
        ]
    )

    @given(st.lists(step_strategy, min_size=1, max_size=4, unique_by=lambda d: next(iter(d))))
    def check(steps: list[dict[str, Any]]) -> None:
        spec = TransformSpec.model_validate(
            {
                "name": "t",
                "input": str(data_dir),
                "output": str(data_dir / "out"),
                "steps": steps,
            }
        )
        sql = compile_sql(spec)
        con = duckdb.connect()
        try:
            con.execute(f"PREPARE stmt AS {sql}")
        finally:
            con.close()

    check()
