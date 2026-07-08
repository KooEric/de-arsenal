"""Gladius CLI (M3 Task 3.4).

compile: 생성될 SQL을 그대로 출력 — 투명성 커맨드, 마법 없음.
run: 변환 실행.
P1: query(즉석 쿼리, 미니 DWH) 서브커맨드 추가 예정.
"""

from pathlib import Path

import typer

app = typer.Typer(help="Gladius — 변환·쿼리. 선언이 SQL로 컴파일된다.")

NOT_IMPLEMENTED_MSG = "not implemented yet — see docs/04-implementation-plan.md M3"


@app.command()
def compile(spec_path: Path) -> None:
    """변환 스펙이 생성할 SQL을 출력한다 (실행하지 않음)."""
    typer.echo(f"error: {NOT_IMPLEMENTED_MSG} (Task 3.4)", err=True)
    raise typer.Exit(1)


@app.command()
def run(spec_path: Path) -> None:
    """변환 실행: Parquet in → DuckDB → Parquet out."""
    typer.echo(f"error: {NOT_IMPLEMENTED_MSG} (Task 3.4)", err=True)
    raise typer.Exit(1)


@app.command()
def query(
    sql: str,
    fmt: str = typer.Option("table", "--format", help="table | csv | jsonl"),
    limit: int = typer.Option(0, help="0 = 제한 없음"),
) -> None:
    """수집·변환 결과 Parquet에 즉석 SQL (미니 DWH)."""
    typer.echo(f"error: {NOT_IMPLEMENTED_MSG} (Task 3.6)", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
