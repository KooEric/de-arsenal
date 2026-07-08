"""steps → SQL 트랜스파일러 (M3 Task 3.2).

각 step이 CTE 하나로 컴파일된다:
  WITH s0 AS (SELECT * FROM read_parquet('...')),
       s1 AS (SELECT * FROM s0 WHERE ...),          -- filter
       s2 AS (SELECT * EXCLUDE (a), a AS b FROM s1) -- rename
  SELECT ... FROM s2                                 -- select

원칙:
- 생성 SQL은 `gladius compile`로 그대로 노출 (투명성 — 마법 없음)
- 식별자는 화이트리스트 + 인용, 값은 바인딩 — 인젝션 안전 (hypothesis 퍼징)
- golden test: 손으로 쓴 SQL과 결과 동치 (docs/05-testing-plan.md)
"""

from gladius.spec import TransformSpec


def compile_sql(spec: TransformSpec) -> str:
    """TransformSpec을 실행 가능한 DuckDB SQL 문자열로 컴파일한다."""
    raise NotImplementedError("M3 Task 3.2 — docs/04-implementation-plan.md")
