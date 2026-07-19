"""Gladius 변환 벤치마크 — 회귀 감지용 참고치, 게이트가 아니다 (M3 Task 3.8).

DuckDB로 ~1GB 합성 Parquet을 생성한 뒤, scenario-D 스타일 변환(filter + cast +
select)을 `gladius.engine.run_transform`으로 3회 실행해 중앙값(median) 소요
시간을 보고한다.

실행:
    uv run python benchmarks/bench_transform.py

측정치는 실행 환경(CPU/디스크/메모리)에 크게 좌우된다 — 이 스크립트의 목적은
"이번 변경이 이전보다 몇 배 느려졌는가"를 잡아내는 참고 기준선이지, CI를 막는
통과/실패 게이트가 아니다. 실측값은 benchmarks/RESULTS.md에 타임스탬프와 함께
기록한다. 테스트가 아니므로 `uv run pytest`로는 실행되지 않는다(`tests/`가 아닌
`benchmarks/`에 위치, 파일명도 `test_*`가 아니다).
"""

from __future__ import annotations

import shutil
import statistics
import tempfile
import time
from pathlib import Path

import duckdb

from gladius.engine import run_transform
from gladius.spec import TransformSpec

# 컬럼 구성(정수 2 + 실수 1 + 저카디널리티 문자열 1 + 32바이트 md5 해시 2개 연결)
# 기준 실측 압축 Parquet 크기는 행당 ~78바이트 (ZSTD 미지정 시 DuckDB 기본
# 압축). 13,000,000행 → 약 1.01GB — 이 스크립트를 실제로 1회 실행해 확인한
# 수치는 benchmarks/RESULTS.md에 있다.
ROW_COUNT = 13_000_000
RUNS = 3

_GENERATE_SQL = """
COPY (
  SELECT
    range AS id,
    (random() * 1000)::DOUBLE AS amount,
    (range % 97) AS quantity,
    ['electronics', 'books', 'toys', 'home', 'sports',
     'garden', 'auto', 'music', 'food', 'office'][(range % 10) + 1] AS category,
    md5(range::VARCHAR || random()::VARCHAR)
      || md5((range + 1)::VARCHAR || random()::VARCHAR) AS payload
  FROM range({n})
) TO {dest} (FORMAT PARQUET)
"""


def _quote_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _generate_input(input_dir: Path, n: int) -> int:
    """DuckDB로 합성 Parquet을 생성하고 실제 디스크 크기(바이트)를 반환한다."""
    input_dir.mkdir(parents=True, exist_ok=True)
    dest = input_dir / "part-0.parquet"
    con = duckdb.connect()
    try:
        con.execute(_GENERATE_SQL.format(n=n, dest=_quote_literal(dest)))
    finally:
        con.close()
    return dest.stat().st_size


def _build_transform_spec(input_dir: Path, output_dir: Path) -> TransformSpec:
    """scenario-D 스타일: filter + cast + select 세 스텝짜리 대표 변환."""
    return TransformSpec.model_validate(
        {
            "name": "bench-transform",
            "input": str(input_dir),
            "steps": [
                {"filter": "quantity > 50"},
                {"cast": {"amount": "double"}},
                {"select": ["id", "amount", "quantity", "category"]},
            ],
            "output": str(output_dir),
        }
    )


def _time_run(spec: TransformSpec) -> float:
    start = time.perf_counter()
    run_transform(spec)
    return time.perf_counter() - start


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="gladius-bench-"))
    input_dir = workdir / "input"
    output_dir = workdir / "output"
    try:
        print(f"generating {ROW_COUNT:,} rows of synthetic Parquet into {input_dir} ...")
        input_bytes = _generate_input(input_dir, ROW_COUNT)
        input_mb = input_bytes / 1_000_000
        print(f"input size: {input_mb:.1f} MB ({input_bytes:,} bytes)")

        spec = _build_transform_spec(input_dir, output_dir)
        durations: list[float] = []
        for i in range(RUNS):
            elapsed = _time_run(spec)
            durations.append(elapsed)
            print(f"  run {i + 1}/{RUNS}: {elapsed:.3f}s")

        median_s = statistics.median(durations)
        throughput = ROW_COUNT / median_s

        print()
        print("=== gladius transform benchmark ===")
        header = f"{'rows':>14} | {'input MB':>10} | {'median s':>10} | {'rows/sec':>14}"
        print(header)
        print(f"{ROW_COUNT:>14,} | {input_mb:>10.1f} | {median_s:>10.3f} | {throughput:>14,.0f}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
