"""Pugio CLI. 재실행은 곧 재개다 — resume 플래그는 존재하지 않는다.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 9
M2 확장: retry, dlq list/retry 서브커맨드 (docs/02-architecture.md "CLI 설계")
"""

from pathlib import Path

import typer
from scorpio import assess_freshness

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
    typer.echo(
        f"done: fetched={report.fetched} written={report.written} "
        f"skipped={report.skipped} quarantined={report.quarantined}"
    )


@app.command()
def status(
    spec_path: Path,
    cost: bool = typer.Option(False, "--cost", help="누적 행·바이트·처리 시간·freshness 표시"),
    max_age_seconds: float | None = typer.Option(
        None,
        "--max-age-seconds",
        min=0.001,
        help="freshness 경보 임계값; stale이면 alert를 출력한다",
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
    if not records:
        typer.echo("no quarantined units")
        return
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
    if spec.source.type == "rest" and spec.source.incremental is not None:
        # 같은 고아 문제의 증분판: 격리는 워터마크 전진을 막지 못하므로, 그 창은
        # 다시 열거되지 않는다 — requeue해도 어떤 실행도 이 unit을 재등록하지 않는다.
        # evidence(DLQ 파일)를 지우지 않고 거부한다. 복구는 그 구간을 명시적
        # `start`로 다시 수집하는 별도 파이프라인이다.
        typer.echo(
            "error: dlq retry is not supported for incremental pipelines "
            "(the watermark has moved past that window, so it is never enumerated again); "
            "re-collect that range with a separate pipeline name and an explicit start",
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


if __name__ == "__main__":
    app()
