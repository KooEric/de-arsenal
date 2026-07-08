"""Arsenal CLI — 단일 진입점. 위임만 하고 로직을 갖지 않는다.

구현: docs/plans/2026-07-08-m4-integration-release.md Task 4.0
"""

from pathlib import Path

import typer

from gladius.cli import app as gladius_app
from pugio.cli import app as pugio_app

app = typer.Typer(help="Arsenal — 원클릭 데이터 스택. init → run → query.")

NOT_IMPLEMENTED_MSG = "not implemented yet — see docs/plans/2026-07-08-m4-integration-release.md"


@app.command()
def init(recipe: str, dest: Path = Path(".")) -> None:
    """레시피로 프로젝트 스캐폴드 (예: arsenal init csv-cleanup)."""
    typer.echo(f"error: {NOT_IMPLEMENTED_MSG} (Task 4.0)", err=True)
    raise typer.Exit(1)


@app.command()
def run(project: Path = Path(".")) -> None:
    """arsenal.yaml 순서대로 수집→변환 일괄 실행. 재실행 = 재개."""
    typer.echo(f"error: {NOT_IMPLEMENTED_MSG} (Task 4.0)", err=True)
    raise typer.Exit(1)


@app.command()
def query(sql: str, fmt: str = typer.Option("table", "--format")) -> None:
    """수집·변환 결과에 즉석 SQL (gladius.engine.query 위임)."""
    typer.echo(f"error: {NOT_IMPLEMENTED_MSG} (Task 4.0)", err=True)
    raise typer.Exit(1)


# 개별 도구는 서브커맨드로 그대로 노출 — 우산은 감싸되 가리지 않는다
app.add_typer(pugio_app, name="collect", help="수집 (pugio)")
app.add_typer(gladius_app, name="transform", help="변환·쿼리 (gladius)")


if __name__ == "__main__":
    app()
