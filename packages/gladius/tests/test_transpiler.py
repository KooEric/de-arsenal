"""steps → SQL 트랜스파일러 스냅샷 테스트 (M3 Task 3.3)."""

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import given
from hypothesis import strategies as st

from gladius.compile.transpiler import compile_sql
from gladius.spec import TransformSpec


def make(
    steps: list[dict[str, Any]], map_: dict[str, str] | None = None, input_: str = "./in"
) -> TransformSpec:
    return TransformSpec.model_validate(
        {
            "name": "t",
            "input": input_,
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


def test_sql_step_replaces_input_placeholder_with_previous_cte() -> None:
    sql = compile_sql(
        make(steps=[{"filter": "state = 'open'"}, {"sql": "SELECT * FROM {input} LIMIT 10"}])
    )
    assert "s2 AS (SELECT * FROM s1 LIMIT 10)" in sql


def test_python_udf_step_compiles_to_private_function_call() -> None:
    sql = compile_sql(
        make(
            steps=[
                {
                    "python": "my_transforms:slugify",
                    "args": ["title"],
                    "output": "slug",
                    "return_type": "VARCHAR",
                }
            ]
        )
    )
    assert 's1 AS (SELECT *, "__gladius_python_udf_0"("title") AS "slug" FROM s0)' in sql


def test_sql_step_compiles_to_valid_duckdb_sql(tmp_path_factory: pytest.TempPathFactory) -> None:
    data_dir = tmp_path_factory.mktemp("sql-step-data")
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2], "amount": [5, 20]}), data_dir / "a.parquet"
    )
    sql = compile_sql(
        TransformSpec.model_validate(
            {
                "name": "sql",
                "input": str(data_dir),
                "output": str(data_dir / "out"),
                "steps": [{"sql": "SELECT id FROM {input} WHERE amount > 10"}],
            }
        )
    )
    con = duckdb.connect()
    try:
        assert con.execute(sql).fetchall() == [(2,)]
    finally:
        con.close()


def test_compile_can_target_explicit_incremental_input_files(tmp_path: Path) -> None:
    spec = make(steps=[{"select": ["id"]}])
    paths = [tmp_path / "a.parquet", tmp_path / "b.parquet"]
    sql = compile_sql(spec, paths)
    assert "read_parquet(['" in sql
    assert "a.parquet" in sql and "b.parquet" in sql


def test_single_quote_in_input_path_is_escaped() -> None:
    # CRITICAL 리뷰 발견 회귀 테스트: input 경로의 작은따옴표는 SQL 리터럴을
    # 깨뜨릴 수 있으므로 이스케이프(작은따옴표 두 배)되어야 한다.
    sql = compile_sql(make(steps=[{"filter": "state = 'open'"}], input_="./in'jected"))
    assert "in''jected" in sql
    assert "in'jected" not in sql.replace("in''jected", "")
    # 단일 read_parquet 호출만 존재해야 한다 — 인젝션이 두 번째 호출/UNION을
    # 만들어내지 않았다는 증거.
    assert sql.count("read_parquet(") == 1


def test_sql_injection_payload_stays_inside_one_string_literal() -> None:
    # 리뷰에서 지적된 인젝션 페이로드: 문자열을 깨고 괄호를 닫은 뒤 UNION SELECT로
    # 마커 값을 빼내려는 시도. 이스케이프되면 전체가 하나의 문자열 리터럴 안에
    # 남아야 하며, 실행해도 마커 값이 노출되지 않아야 한다(DuckDB가 존재하지
    # 않는 파일 경로로 취급해 IO 에러를 내야 한다).
    payload = "./in', union_by_name=true)) UNION SELECT 1337 AS pwn --"
    spec = make(steps=[{"select": ["id"]}], input_=payload)
    sql = compile_sql(spec)

    escaped_payload = payload.replace("'", "''")
    assert escaped_payload in sql
    assert sql.count("read_parquet(") == 1

    # 실행하면 UNION이 성공해 1337 마커가 노출되는 게 아니라, 페이로드 전체가
    # (존재하지 않는) 파일 glob 패턴 리터럴로 취급되어 IO 에러가 나야 한다 —
    # 인젝션이 SQL 구조가 아니라 데이터로 남았다는 증거.
    con = duckdb.connect()
    try:
        with pytest.raises(duckdb.Error):
            con.execute(sql)
    finally:
        con.close()


def test_non_last_select_narrows_columns_for_later_steps() -> None:
    # MINOR 리뷰 발견 회귀 테스트: select가 마지막 step이 아니면 그 지점에서
    # 실제로 projection을 수행하는 CTE가 되어야 한다 (SELECT * 가 아니라
    # 선택된 컬럼 목록).
    sql = compile_sql(make(steps=[{"select": ["id", "state"]}, {"filter": "state = 'open'"}]))
    assert 's1 AS (SELECT "id", "state" FROM s0)' in sql
    assert "SELECT * FROM s1 WHERE state = 'open'" in sql
    assert "SELECT * FROM s0)" not in sql  # select CTE 본문에 SELECT * 가 남아있으면 안 된다


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
