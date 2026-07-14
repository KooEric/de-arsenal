"""Gladius CLI (M3 Task 3.6).

compile: 생성될 SQL을 그대로 출력 — 투명성 커맨드, 마법 없음.
run: 변환 실행.
query: 수집·변환 결과 Parquet에 즉석 SQL — 미니 DWH의 "아하 모먼트".
"""

import json
import sys
from pathlib import Path

import duckdb
import pyarrow.csv as pa_csv
import typer

from arsenal_core.errors import ArsenalError
from gladius.compile import compile_sql
from gladius.engine import query as run_query
from gladius.engine import run_transform
from gladius.loader import load_transform

app = typer.Typer(help="Gladius — 변환·쿼리. 선언이 SQL로 컴파일된다.")


@app.command(name="compile")
def compile_cmd(spec_path: Path) -> None:
    """변환 스펙이 생성할 SQL을 출력한다 (실행하지 않음)."""
    try:
        spec = load_transform(spec_path)
        sql = compile_sql(spec)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(sql)


@app.command(name="run")
def run_cmd(spec_path: Path) -> None:
    """변환 실행: Parquet in → DuckDB → Parquet out."""
    try:
        spec = load_transform(spec_path)
        out = run_transform(spec)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"done: {out}")


@app.command(name="query")
def query_cmd(
    sql: str,
    fmt: str = typer.Option("table", "--format", help="table | csv | jsonl"),
    limit: int = typer.Option(0, help="0 = 제한 없음"),
) -> None:
    """수집·변환 결과 Parquet에 즉석 SQL (미니 DWH)."""
    effective_sql = sql if limit <= 0 else f"SELECT * FROM ({sql}) AS _gladius_query LIMIT {limit}"
    try:
        result = run_query(effective_sql)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    if fmt == "table":
        duckdb.sql("SELECT * FROM result").show()  # pyright: ignore[reportUnknownMemberType]
    elif fmt == "csv":
        pa_csv.write_csv(  # pyright: ignore[reportPrivateImportUsage, reportUnknownMemberType]
            result, sys.stdout.buffer
        )
    elif fmt == "jsonl":
        for row in result.to_pylist():
            typer.echo(json.dumps(row))
    else:
        typer.echo(f"error: unsupported --format: {fmt!r} (table | csv | jsonl)", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
