"""Pugio CLI. 재실행은 곧 재개다 — resume 플래그는 존재하지 않는다.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 9
M2 확장: retry, dlq list/retry 서브커맨드 (docs/02-architecture.md "CLI 설계")
"""

from pathlib import Path

import typer

from arsenal_core.errors import ArsenalError
from arsenal_core.spec import load_pipeline
from arsenal_core.state import StateStore
from pugio.dlq import dlq_dir, read_reason, remove_dlq
from pugio.runner import run_pipeline

app = typer.Typer(help="Pugio — 수집·전송. 재실행은 곧 재개다.")
dlq_app = typer.Typer(help="dead-letter queue — 격리된 unit 조회·재시도")
app.add_typer(dlq_app, name="dlq")


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


@dlq_app.command("list")
def dlq_list(spec_path: Path) -> None:
    """격리된 unit 목록 — unit_key + 위반 사유(DLQ 사유 JSON에서 읽는다)."""
    try:
        spec = load_pipeline(spec_path)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        records = store.quarantined(spec.name)
    finally:
        store.close()
    if not records:
        typer.echo("no quarantined units")
        return
    for rec in records:
        json_path = dlq_dir(spec.state_dir, spec.name) / f"{rec.unit_id}.json"
        unit_key = rec.unit_id
        if json_path.exists():
            reason = read_reason(json_path)
            unit_key = str(reason.get("unit_key", rec.unit_id))
        typer.echo(f"{unit_key}: {rec.last_error}")


@dlq_app.command("retry")
def dlq_retry(spec_path: Path, unit: str = typer.Option(..., "--unit")) -> None:
    """격리된 unit을 pending으로 되돌리고 DLQ 파일을 지운다. 다음 run이 재수집한다."""
    try:
        spec = load_pipeline(spec_path)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        store.requeue(unit)
    finally:
        store.close()
    remove_dlq(spec.state_dir, spec.name, unit)
    typer.echo(f"requeued {unit}")


if __name__ == "__main__":
    app()
