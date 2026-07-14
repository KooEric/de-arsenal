"""steps → SQL 트랜스파일러 (M3 Task 3.3).

각 step이 CTE 하나로 컴파일된다:
  WITH s0 AS (SELECT * FROM read_parquet('...')),
       s1 AS (SELECT * FROM s0 WHERE ...),          -- filter
       s2 AS (SELECT * EXCLUDE (a), a AS b FROM s1) -- rename
  SELECT ... FROM s2                                 -- select

원칙:
- 생성 SQL은 `gladius compile`로 그대로 노출 (투명성 — 마법 없음)
- 식별자는 화이트리스트 + 인용, 값은 바인딩 — 인젝션 안전 (hypothesis 퍼징)
- golden test: 손으로 쓴 SQL과 결과 동치 (docs/05-testing-plan.md)

select step은 CTE가 아니라 최종 SELECT로 처리한다. 마지막 위치가 아닌 select는
그 지점에서 실제 projection을 수행해(중간 select 허용) 이후 step이 좁혀진 컬럼
집합 위에서 동작하게 한다.
"""

from gladius.compile.ident import normalize_type, quote_ident, quote_str_literal
from gladius.spec import (
    CastStep,
    DedupStep,
    DeriveStep,
    FilterStep,
    RenameStep,
    SelectStep,
    Step,
    TransformSpec,
)


def compile_sql(spec: TransformSpec) -> str:
    """TransformSpec을 실행 가능한 DuckDB SQL 문자열로 컴파일한다."""
    src = str(spec.input).rstrip("/")
    src_glob = quote_str_literal(f"{src}/**/*.parquet")
    ctes = [f"s0 AS (SELECT * FROM read_parquet({src_glob}, union_by_name=true))"]
    idx = 0
    if spec.map:
        idx += 1
        cols = ", ".join(f"{expr} AS {quote_ident(new)}" for new, expr in spec.map.items())
        ctes.append(f"s{idx} AS (SELECT {cols} FROM s{idx - 1})")
    steps = spec.steps
    for i, step in enumerate(steps):
        is_last = i == len(steps) - 1
        if isinstance(step, SelectStep) and is_last:
            continue  # 마지막 select는 CTE가 아니라 최종 SELECT에서 처리
        idx += 1
        ctes.append(f"s{idx} AS ({_compile_step(step, f's{idx - 1}')})")
    final = _final_select(steps, f"s{idx}")
    return "WITH " + ",\n".join(ctes) + "\n" + final


def _compile_step(step: Step, prev: str) -> str:
    match step:
        case FilterStep(filter=cond):
            return f"SELECT * FROM {prev} WHERE {cond}"
        case RenameStep(rename=m):
            ex = ", ".join(quote_ident(o) for o in m)
            al = ", ".join(f"{quote_ident(o)} AS {quote_ident(n)}" for o, n in m.items())
            return f"SELECT * EXCLUDE ({ex}), {al} FROM {prev}"
        case CastStep(cast=m):
            rep = ", ".join(
                f"CAST({quote_ident(c)} AS {normalize_type(t)}) AS {quote_ident(c)}"
                for c, t in m.items()
            )
            return f"SELECT * REPLACE ({rep}) FROM {prev}"
        case DedupStep(dedup=keys):
            part = ", ".join(quote_ident(k) for k in keys)
            return (
                f"SELECT * FROM {prev} QUALIFY row_number() "
                f"OVER (PARTITION BY {part} ORDER BY {part}) = 1"
            )
        case DeriveStep(derive=m):
            dv = ", ".join(f"{expr} AS {quote_ident(n)}" for n, expr in m.items())
            return f"SELECT *, {dv} FROM {prev}"
        case SelectStep(select=cols):
            projected = ", ".join(quote_ident(c) for c in cols)
            return f"SELECT {projected} FROM {prev}"


def _final_select(steps: list[Step], last: str) -> str:
    if steps and isinstance(steps[-1], SelectStep):
        cols = ", ".join(quote_ident(c) for c in steps[-1].select)
        return f"SELECT {cols} FROM {last}"
    return f"SELECT * FROM {last}"
