"""Eval 하네스 — 골든 SQL과 생성 SQL의 실행 결과를 대조하고 실패 모드를 자동 분류한다.

실패 모드는 파이프라인 단계 순서로 판정한다(앞 단계 실패가 뒷 단계 실패의 원인이므로):
  RETRIEVAL_MISS      골든이 쓰는 테이블이 검색 결과에 없음        → retrieve.py 문제
  NON_SELECT          LLM이 SELECT 아닌 것을 냄                  → 프롬프트/모델 문제
  UNANSWERABLE        모델이 답 불가를 선언(정답이 answerable이면 오류)
  HALLUCINATED_COLUMN 카탈로그에 없는 컬럼 참조                   → 스키마 렌더/모델 문제
  HALLUCINATED_TABLE  카탈로그에 없는 경로/테이블 참조
  EXEC_ERROR          위 어느 것도 아닌데 DuckDB 실행 실패(문법 등)
  WRONG_SHAPE         실행됐지만 컬럼 수/행 수가 골든과 다름       → 질문 해석 오류(집계 축·필터)
  WRONG_RESULT        모양은 같은데 값이 다름                     → 의미 오류(조건·리터럴)
  CORRECT
분류가 자동이어야 50건 이상을 반복 측정할 수 있다. 수동 라벨은 notes 컬럼에.
"""

import json
import re
import typing as t
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from pydantic import BaseModel, ConfigDict

from arsenal_core.errors import FatalError
from augur.catalog import Catalog
from augur.generate import Trace, ask, execute
from augur.llm import Provider

FAILURE_MODES = (
    "RETRIEVAL_MISS",
    "NON_SELECT",
    "UNANSWERABLE",
    "HALLUCINATED_COLUMN",
    "HALLUCINATED_TABLE",
    "EXEC_ERROR",
    "WRONG_RESULT",
    "WRONG_SHAPE",
    "CORRECT",
)
PATH_RE = re.compile(r"'([^']+\.parquet)'")


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    question: str
    golden_sql: str
    tables: tuple[str, ...]  # 골든이 필요로 하는 카탈로그 테이블명
    answerable: bool = True
    notes: str = ""


def load_cases(path: Path) -> tuple[EvalCase, ...]:
    if not path.exists():
        raise FatalError(f"cases file not found: {path}")
    data = t.cast(dict[str, t.Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    cases = tuple(EvalCase.model_validate(c) for c in data.get("cases", []))
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise FatalError("duplicate case ids")
    return cases


def _normalize(v: t.Any) -> t.Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, float):
        return round(v, 6)
    return v


def rows_equal(a: list[tuple[t.Any, ...]], b: list[tuple[t.Any, ...]]) -> bool:
    """순서 무시, 값 정규화 후 비교. ORDER BY가 질문의 일부인 케이스는 notes로 표시하고
    골든에 순서를 넣되, 여기서는 집합 비교만 한다(P0 한계)."""
    na = sorted((tuple(_normalize(x) for x in r) for r in a), key=repr)
    nb = sorted((tuple(_normalize(x) for x in r) for r in b), key=repr)
    return na == nb


def classify(
    case: EvalCase,
    trace: Trace,
    catalog: Catalog,
    golden_rows: list[tuple[t.Any, ...]],
    generated_rows: list[tuple[t.Any, ...]],
) -> str:
    missing = [tb for tb in case.tables if tb not in trace.retrieved_tables]
    if missing:
        return "RETRIEVAL_MISS"
    if trace.error and trace.error.startswith("NON_SELECT"):
        return "NON_SELECT"
    if "UNANSWERABLE" in trace.sql.upper():
        return "CORRECT" if not case.answerable else "UNANSWERABLE"
    known_paths = {d.path for d in catalog.tables}
    if any(p not in known_paths for p in PATH_RE.findall(trace.sql)):
        return "HALLUCINATED_TABLE"
    if not trace.executed:
        if trace.error and "not found in FROM clause" in trace.error:
            return "HALLUCINATED_COLUMN"
        return "EXEC_ERROR"
    if not case.answerable:
        return "WRONG_RESULT"  # 답 못 해야 하는데 답함
    if rows_equal(golden_rows, generated_rows):
        return "CORRECT"
    same_shape = len(golden_rows) == len(generated_rows) and (
        not golden_rows or len(golden_rows[0]) == len(generated_rows[0])
    )
    return "WRONG_RESULT" if same_shape else "WRONG_SHAPE"


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    question: str
    failure_mode: str
    retrieved_tables: str
    golden_sql: str
    generated_sql: str
    error: str | None
    repair_attempts: int
    latency_ms: int
    input_tokens: int
    output_tokens: int
    provider: str
    model: str
    notes: str


def run_case(
    case: EvalCase, catalog: Catalog, provider: Provider, *, top_k: int
) -> tuple[EvalResult, Trace]:
    try:
        _, golden_rows = execute(case.golden_sql)
    except duckdb.Error as e:
        raise FatalError(f"golden SQL failed for {case.id}: {e}") from e
    trace, generated_rows = ask(case.question, catalog, provider, top_k=top_k)
    mode = classify(case, trace, catalog, golden_rows, generated_rows)
    result = EvalResult(
        case.id,
        case.question,
        mode,
        ",".join(trace.retrieved_tables),
        case.golden_sql,
        trace.sql,
        trace.error,
        trace.repair_attempts,
        trace.latency_ms,
        trace.input_tokens,
        trace.output_tokens,
        trace.provider,
        trace.model,
        case.notes,
    )
    return result, trace


def run_eval(
    cases: tuple[EvalCase, ...],
    catalog: Catalog,
    provider: Provider,
    *,
    top_k: int = 3,
    run_id: str,
    out_dir: Path,
) -> list[EvalResult]:
    """결과는 out_dir/run_id.parquet + traces/run_id.jsonl. Parquet으로 남기는 이유:
    `gladius query`로 바로 집계 — 도구가 도구를 평가한다(dogfooding)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "traces" / f"{run_id}.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[EvalResult] = []
    with trace_path.open("w", encoding="utf-8") as tf:
        for case in cases:
            result, trace = run_case(case, catalog, provider, top_k=top_k)
            results.append(result)
            tf.write(
                json.dumps(
                    {"run_id": run_id, "case_id": case.id, **asdict(trace)}, ensure_ascii=False
                )
                + "\n"
            )
    table = pa.Table.from_pylist([{"run_id": run_id, **asdict(r)} for r in results])
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        table, out_dir / f"{run_id}.parquet", compression="zstd"
    )
    return results


def summarize(results: list[EvalResult]) -> dict[str, int]:
    counts = {m: 0 for m in FAILURE_MODES}
    for r in results:
        counts[r.failure_mode] += 1
    return {k: v for k, v in counts.items() if v or k == "CORRECT"}
