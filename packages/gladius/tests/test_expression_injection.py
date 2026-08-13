"""표현식 슬롯의 문(statement) 주입 방어.

신뢰 모델은 dbt와 같다 — filter/derive/map의 표현식은 사용자 자신의 SQL이므로
내용을 검증하지 않는다. 다만 표현식이 `COPY (...) TO ...` 템플릿 안에 삽입되므로,
**구조를 깨서 새 문을 여는 것**은 막아야 한다. 이건 정책이 아니라 계약이다:
sql 스텝은 이미 ';'를 금지하고 있었고, filter/derive/map만 빠져 있었다.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from arsenal_core.errors import FatalError
from gladius.compile.transpiler import compile_sql
from gladius.engine import run_transform
from gladius.spec import TransformSpec

BASE = {"name": "t", "input": "./in", "output": "./out"}

# 실제로 재현했던 페이로드 — CTE와 COPY 래퍼를 닫고 두 번째 문을 연다.
STATEMENT_INJECTION = (
    "1=1) SELECT * FROM s0) TO './PWNED.csv' (FORMAT CSV); COPY (WITH s1 AS (SELECT 1"
)


def test_filter_rejects_statement_separator() -> None:
    with pytest.raises(ValidationError, match="';'"):
        TransformSpec.model_validate({**BASE, "steps": [{"filter": STATEMENT_INJECTION}]})


def test_derive_rejects_statement_separator() -> None:
    with pytest.raises(ValidationError, match="';'"):
        TransformSpec.model_validate({**BASE, "steps": [{"derive": {"x": "1); DROP TABLE t"}}]})


def test_map_rejects_statement_separator() -> None:
    with pytest.raises(ValidationError, match="';'"):
        TransformSpec.model_validate({**BASE, "map": {"x": "1); DROP TABLE t"}})


def test_filter_expression_is_parenthesised() -> None:
    """괄호로 감싸면 여분의 ')'가 스코프 탈출이 아니라 파싱 에러가 된다."""
    spec = TransformSpec.model_validate({**BASE, "steps": [{"filter": "state = 'open'"}]})
    assert "WHERE (state = 'open')" in compile_sql(spec)


def test_derive_expression_is_parenthesised() -> None:
    spec = TransformSpec.model_validate({**BASE, "steps": [{"derive": {"n": "a + b"}}]})
    assert '(a + b) AS "n"' in compile_sql(spec)


def test_semicolonless_scope_escape_fails_instead_of_writing_a_file(tmp_path: Path) -> None:
    """세미콜론 없이 괄호만으로 COPY 래퍼를 닫으려는 시도는 실행에 실패해야 한다.

    이전에는 filter가 그대로 삽입돼 공격자가 지정한 경로에 파일이 생겼다.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    (tmp_path / "in").mkdir()
    pq.write_table(pa.table({"id": [1]}), tmp_path / "in" / "a.parquet")  # pyright: ignore[reportUnknownMemberType]
    target = tmp_path / "PWNED.csv"

    spec = TransformSpec.model_validate(
        {
            "name": "t",
            "input": str(tmp_path / "in"),
            "output": str(tmp_path / "out"),
            "steps": [{"filter": f"1=1) SELECT * FROM s0) TO '{target}' (FORMAT CSV"}],
        }
    )
    with pytest.raises(FatalError):
        run_transform(spec)
    assert not target.exists()
