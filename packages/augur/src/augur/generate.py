"""생성 — 검색된 스키마 + 질문 → SQL. 모든 실행은 Trace 한 건을 남긴다.

설계 원칙:
- 프롬프트는 결정적(스키마 렌더 순서 고정, temperature 0). 같은 입력이면 같은 프롬프트.
- SELECT만 허용. DDL/DML은 실행 전에 차단(FatalError가 아니라 trace에 NON_SELECT로 기록).
- 자가 수정(repair)은 최대 1회. 무한 재시도는 실패를 숨긴다.
- LLM 출력 원문(raw)을 그대로 저장. 후처리로 잃는 정보가 없어야 사후 분석이 된다.
"""

import hashlib
import json
import re
import time
import typing as t
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

from augur.catalog import Catalog, SchemaDoc
from augur.llm import Provider
from augur.retrieve import retrieve

MAX_REPAIR_ATTEMPTS = 1
FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
WRITE_RE = re.compile(
    r"^\s*(insert|update|delete|create|drop|alter|truncate|copy|attach|detach|pragma|set|call"
    r"|export|import|install|load|vacuum|merge)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You write DuckDB SQL for a local Parquet lake.
Rules:
- Output exactly one SQL statement and nothing else. No prose, no markdown fence.
- Reference tables ONLY by the quoted source path given in the schema,
  e.g. FROM './data/orders/*.parquet'.
- Use only columns listed in the schema.
- If the question cannot be answered from the schema, output exactly:
  SELECT 'UNANSWERABLE' AS reason
- Read-only: SELECT / WITH only."""


def build_user_prompt(question: str, docs: tuple[SchemaDoc, ...]) -> str:
    schema = "\n".join(d.render() for d in docs)
    return f"Schema:\n{schema}\n\nQuestion: {question}\nSQL:"


def extract_sql(raw: str) -> str:
    m = FENCE_RE.search(raw)
    text = m.group(1) if m else raw
    return text.strip().rstrip(";").strip()


def is_select(sql: str) -> bool:
    """쓰기 문장만 차단한다. 'SELEC ...' 같은 오타는 실행 단계에서 EXEC_ERROR로 잡히게 둔다 —
    가드가 문법 오류까지 삼키면 실패 모드가 섞인다."""
    return bool(sql.strip()) and not WRITE_RE.match(sql)


@dataclass
class Trace:
    question: str
    retrieved_tables: list[str]
    prompt_hash: str
    raw_completion: str
    sql: str
    executed: bool = False
    error: str | None = None
    repair_attempts: int = 0
    repair_raw: list[str] = field(default_factory=list[str])
    row_count: int | None = None
    columns: list[str] = field(default_factory=list[str])
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    provider: str = ""
    model: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def execute(sql: str) -> tuple[list[str], list[tuple[t.Any, ...]]]:
    con = duckdb.connect()
    try:
        rel = con.execute(sql)
        cols = [str(d[0]) for d in (rel.description or [])]
        rows = rel.fetchall()
        return cols, rows
    finally:
        con.close()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def ask(
    question: str, catalog: Catalog, provider: Provider, *, top_k: int = 3
) -> tuple[Trace, list[tuple[t.Any, ...]]]:
    """질문 → (Trace, rows). 실패해도 예외를 던지지 않고 Trace.error에 기록한다.
    이유: eval은 실패를 세는 도구라, 실패가 예외로 터지면 집계가 안 된다."""
    started = time.perf_counter()
    docs = retrieve(question, catalog, top_k=top_k)
    user = build_user_prompt(question, docs)
    completion = provider.complete(SYSTEM_PROMPT, user)
    sql = extract_sql(completion.text)
    trace = Trace(
        question=question,
        retrieved_tables=[d.table for d in docs],
        prompt_hash=_hash(SYSTEM_PROMPT + user),
        raw_completion=completion.text,
        sql=sql,
        provider=provider.name,
        model=completion.model,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
    )
    rows: list[tuple[t.Any, ...]] = []
    if not is_select(sql):
        trace.error = "NON_SELECT: statement rejected before execution"
    else:
        rows = _execute_with_repair(trace, user, provider)
    trace.latency_ms = int((time.perf_counter() - started) * 1000)
    return trace, rows


def _execute_with_repair(trace: Trace, user: str, provider: Provider) -> list[tuple[t.Any, ...]]:
    sql = trace.sql
    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        try:
            cols, rows = execute(sql)
            trace.executed, trace.sql, trace.columns, trace.row_count = True, sql, cols, len(rows)
            trace.error = None
            return rows
        except duckdb.Error as e:
            trace.error = str(e).splitlines()[0]
            if attempt >= MAX_REPAIR_ATTEMPTS:
                return []
            repair_user = f"{user}\n\nPrevious SQL:\n{sql}\nDuckDB error: {trace.error}\nFixed SQL:"
            completion = provider.complete(SYSTEM_PROMPT, repair_user)
            trace.repair_attempts = attempt + 1
            trace.repair_raw.append(completion.text)
            trace.input_tokens += completion.input_tokens
            trace.output_tokens += completion.output_tokens
            sql = extract_sql(completion.text)
            if not is_select(sql):
                trace.error = "NON_SELECT: repair produced non-SELECT"
                return []
    return []


def append_trace(path: Path, trace: Trace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(trace.to_json() + "\n")
