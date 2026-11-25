"""Pugio CLI. 재실행은 곧 재개다 — resume 플래그는 존재하지 않는다.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 9
M2 확장: retry, dlq list/retry 서브커맨드 (docs/02-architecture.md "CLI 설계")
"""

from pathlib import Path

import typer
from scorpio import assess_freshness

from arsenal_core.errors import ArsenalError, FatalError
from arsenal_core.spec import load_pipeline
from arsenal_core.state import StateStore
from pugio.ddl import trino_hive_external_table
from pugio.dlq import dlq_dir, read_reason, remove_dlq, split_rejected_rows
from pugio.runner import run_pipeline
from pugio.transfer.runner import run_transfer, transfer_status
from pugio.transfer.spec import load_transfer

app = typer.Typer(help="Pugio — 수집·전송. 재실행은 곧 재개다.")
dlq_app = typer.Typer(help="dead-letter queue — 격리된 unit 조회·재시도")
app.add_typer(dlq_app, name="dlq")
transfer_app = typer.Typer(
    help="query → S3 → Redshift COPY. The source engine exports; rows skip this machine."
)
app.add_typer(transfer_app, name="transfer")


@app.command()
def run(spec_path: Path) -> None:
    """파이프라인 실행. 중단됐던 실행은 자동으로 이어서 한다."""
    try:
        spec = load_pipeline(spec_path)
        report = run_pipeline(spec)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(
        f"done: fetched={report.fetched} written={report.written} "
        f"skipped={report.skipped} quarantined={report.quarantined}"
        + (f" rejected_rows={report.rejected_rows}" if report.rejected_rows else "")
    )


@app.command()
def status(
    spec_path: Path,
    cost: bool = typer.Option(False, "--cost", help="누적 행·바이트·처리 시간·freshness 표시"),
    max_age_seconds: float | None = typer.Option(
        None,
        "--max-age-seconds",
        min=0.001,
        help="freshness 경보 임계값; stale이면 alert를 출력하고 exit 1",
    ),
) -> None:
    """unit 상태 요약. ``--cost``로 P0 단위 지표의 누적값도 표시한다."""
    try:
        spec = load_pipeline(spec_path)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    store = StateStore(Path(spec.state_dir) / f"{spec.name}.db")
    try:
        counts = store.counts(spec.name)
        metrics = store.pipeline_metrics(spec.name) if cost else None
        freshness = (
            assess_freshness(
                store.pipeline_metrics(spec.name).last_completed_at,
                max_age_seconds=max_age_seconds,
            )
            if max_age_seconds is not None
            else None
        )
    finally:
        store.close()
    if not counts:
        typer.echo("no runs yet")
        return
    for state, n in sorted(counts.items()):
        typer.echo(f"{state}: {n}")
    rejected = split_rejected_rows(spec.state_dir, spec.name)
    if rejected:
        typer.echo(f"rejected_rows: {rejected} (on_violation: split; see `pugio dlq list`)")
    if metrics is not None:
        typer.echo(
            f"cost: units={metrics.completed_units} rows={metrics.row_count} "
            f"bytes={metrics.byte_count} duration_ms={metrics.duration_ms} "
            f"last_completed_at={metrics.last_completed_at or 'none'}"
        )
    if freshness is not None:
        typer.echo(
            f"freshness: status={freshness.status} "
            f"age_seconds={freshness.age_seconds if freshness.age_seconds is not None else 'none'} "
            f"limit_seconds={freshness.max_age_seconds}"
        )
        if freshness.alert:
            typer.echo(f"alert: freshness {freshness.message}", err=True)
            raise typer.Exit(1)


@app.command()
def ddl(
    spec_path: Path,
    table: str | None = typer.Option(None, "--table", help="Table name (default: pipeline name)."),
    schema: str = typer.Option("default", "--schema", help="Trino schema."),
    catalog: str = typer.Option("hive", "--catalog", help="Trino catalog (Hive connector)."),
) -> None:
    """Print CREATE TABLE for a Trino Hive external table over a Parquet sink.

    Uses the schema recorded by the pipeline's last run. Prints only; run the
    statement in Trino yourself. An external table has one fixed schema, so pin
    CSV column types (`source.column_types`) if files may disagree.
    """
    try:
        spec = load_pipeline(spec_path)
        if spec.sink.type != "parquet":
            hint = (
                " (Trino reads an iceberg sink through its catalog; no DDL is needed)"
                if spec.sink.type == "iceberg"
                else ""
            )
            raise FatalError(
                f"pugio ddl describes a parquet sink; this pipeline writes to "
                f"{spec.sink.type}{hint}"
            )
        state_file = Path(spec.state_dir) / f"{spec.name}.db"
        if not state_file.exists():
            raise FatalError(f"no recorded schema for {spec.name!r}: run the pipeline once first")
        store = StateStore(state_file)
        try:
            recorded = store.last_schema(spec.name)
        finally:
            store.close()
        if recorded is None:
            raise FatalError(f"no recorded schema for {spec.name!r}: run the pipeline once first")
        statement = trino_hive_external_table(
            schema_json=recorded,
            location=spec.sink.path,
            catalog=catalog,
            schema=schema,
            table=table or spec.name.replace("-", "_"),
        )
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(statement)


@dlq_app.command("list")
def dlq_list(spec_path: Path) -> None:
    """격리된 unit 목록 — 한 줄에 `{unit_id}  {unit_key}  {reason}`.

    unit_id가 맨 앞의 복사-붙여넣기 가능한 식별자다: `dlq retry --unit`은
    unit_id로 매칭하므로 (M2-E FIX 1), 사람이 읽기 좋은 unit_key만 보고
    복사하면 store.requeue()가 0행을 갱신하고도 조용히 성공하는 것처럼 보였다.
    unit_key + reason은 읽기용으로 같은 줄에 덧붙인다.
    """
    try:
        spec = load_pipeline(spec_path)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    store = StateStore(Path(spec.state_dir) / f"{spec.name}.db")
    try:
        records = store.quarantined(spec.name)
    finally:
        store.close()
    # on_violation: split은 unit을 done으로 두고 걸러진 행만 DLQ에 남긴다.
    split_reasons = [
        reason
        for path in sorted(dlq_dir(spec.state_dir, spec.name).glob("*.json"))
        if (reason := read_reason(path)).get("mode") == "split"
    ]
    if not records and not split_reasons:
        typer.echo("no quarantined units")
        return
    for reason in split_reasons:
        typer.echo(
            f"{reason.get('unit_id')}  {reason.get('unit_key')}  "
            f"split: {reason.get('rejected_rows')} rows rejected {reason.get('violations')}"
        )
    for rec in records:
        json_path = dlq_dir(spec.state_dir, spec.name) / f"{rec.unit_id}.json"
        unit_key = rec.unit_id
        if json_path.exists():
            reason = read_reason(json_path)
            unit_key = str(reason.get("unit_key", rec.unit_id))
        typer.echo(f"{rec.unit_id}  {unit_key}  {rec.last_error}")


@dlq_app.command("retry")
def dlq_retry(spec_path: Path, unit: str = typer.Option(..., "--unit")) -> None:
    """격리된 unit을 pending으로 되돌리고 DLQ 파일을 지운다. 다음 run이 재수집한다.

    `--unit`은 `dlq list`가 첫 컬럼으로 찍는 unit_id를 받는다 (M2-E FIX 1).

    M2-E FIX 2 (P0 스코프 락): REST cursor/link 페이지네이션은 프론티어 커서가
    앞으로만 전진해서, 격리된 과거 페이지의 unit_key(`cursor=<val>`)를 재실행이
    다시는 생성할 수 없다 — requeue해도 다음 run이 그 unit을 절대 재등록하지
    않으므로 영원히 고아가 된다. 이 경우엔 evidence(DLQ 파일)도 지우지 않고
    거부한다. offset/page/file/database/python은 unit_key가 결정적으로
    재생산되므로 그대로 재시도된다.
    """
    try:
        spec = load_pipeline(spec_path)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    if spec.source.type == "rest" and spec.source.pagination.mode in ("cursor", "link"):
        typer.echo(
            "error: dlq retry is not supported for cursor/link pagination in P0 "
            "(forward-only cursor cannot regenerate a past page)",
            err=True,
        )
        raise typer.Exit(1)
    store = StateStore(Path(spec.state_dir) / f"{spec.name}.db")
    try:
        updated = store.requeue(unit)
    finally:
        store.close()
    if updated == 0:
        typer.echo(f"error: no quarantined unit with id {unit}", err=True)
        raise typer.Exit(1)
    remove_dlq(spec.state_dir, spec.name, unit)
    typer.echo(f"requeued {unit}")


@transfer_app.command("run")
def transfer_run(
    spec_path: Path,
    reexport: bool = typer.Option(
        False,
        "--reexport",
        help="Run the source query again for units whose export an earlier run saved",
    ),
) -> None:
    """Export each unit with the source engine, then COPY it into Redshift. Reruns resume."""
    try:
        report = run_transfer(load_transfer(spec_path), reexport=reexport)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(
        f"done: loaded={report.loaded} skipped={report.skipped} rows={report.rows} "
        f"exported={report.exported} reused_exports={report.reused_exports}"
    )


@transfer_app.command("status")
def transfer_status_command(spec_path: Path) -> None:
    """Unit counts by state, and the error of each failed unit."""
    try:
        status = transfer_status(load_transfer(spec_path))
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    if not status.counts:
        typer.echo("no runs yet")
        return
    for state, n in sorted(status.counts.items()):
        typer.echo(f"{state}: {n}")
    for failed in status.failed:
        typer.echo(f"failed {failed.unit_key}: {failed.error}")


if __name__ == "__main__":
    app()
