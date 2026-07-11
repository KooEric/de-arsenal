"""Pugio CLI. 재실행은 곧 재개다 — resume 플래그는 존재하지 않는다.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 9
M2 확장: retry, dlq list/retry 서브커맨드 (docs/02-architecture.md "CLI 설계")
"""

from pathlib import Path

import typer

from arsenal_core.errors import ArsenalError
from arsenal_core.spec import load_pipeline
from arsenal_core.state import StateStore
from pugio.runner import run_pipeline

app = typer.Typer(help="Pugio — 수집·전송. 재실행은 곧 재개다.")


@app.command()
def run(spec_path: Path) -> None:
    """파이프라인 실행. 중단됐던 실행은 자동으로 이어서 한다."""
    try:
        spec = load_pipeline(spec_path)
        report = run_pipeline(spec)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"done: fetched={report.fetched} written={report.written} skipped={report.skipped}")


@app.command()
def status(spec_path: Path) -> None:
    """unit 상태 요약."""
    try:
        spec = load_pipeline(spec_path)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        counts = store.counts(spec.name)
    finally:
        store.close()
    if not counts:
        typer.echo("no runs yet")
        return
    for state, n in sorted(counts.items()):
        typer.echo(f"{state}: {n}")


if __name__ == "__main__":
    app()
