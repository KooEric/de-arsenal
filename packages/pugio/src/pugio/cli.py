"""Pugio CLI. 재실행은 곧 재개다 — resume 플래그는 존재하지 않는다.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 9
M2 확장: retry, dlq list/retry 서브커맨드 (docs/02-architecture.md "CLI 설계")
"""

from pathlib import Path

import typer

app = typer.Typer(help="Pugio — 수집·전송. 재실행은 곧 재개다.")

NOT_IMPLEMENTED_MSG = "not implemented yet — see docs/plans/2026-07-08-m1-core-foundation.md"


@app.command()
def run(spec_path: Path) -> None:
    """파이프라인 실행. 중단됐던 실행은 자동으로 이어서 한다."""
    typer.echo(f"error: {NOT_IMPLEMENTED_MSG} (Task 9)", err=True)
    raise typer.Exit(1)


@app.command()
def status(spec_path: Path) -> None:
    """unit 상태 요약 (done/pending/failed/quarantined 수)."""
    typer.echo(f"error: {NOT_IMPLEMENTED_MSG} (Task 9)", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
