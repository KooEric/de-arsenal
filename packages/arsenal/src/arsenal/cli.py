"""Arsenal CLI — 단일 진입점. 위임만 하고 로직을 갖지 않는다.

구현: docs/plans/2026-07-08-m4-integration-release.md Task 4.0
"""

import contextlib
import glob
import importlib
import json
import os
import sys
from collections.abc import Generator, Iterator
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.csv as pa_csv
import typer

from arsenal import stages
from arsenal.project import ArsenalProject, DbtProjectRef, DistributedRef, StreamRef, load_project
from arsenal_core.errors import ArsenalError, FatalError
from arsenal_core.spec import load_pipeline
from gladius import engine as gladius_engine
from gladius.cli import app as gladius_app
from gladius.compile import compile_sql
from gladius.loader import load_transform
from pugio.cli import app as pugio_app
from pugio.runner import run_pipeline as _pugio_run_pipeline

app = typer.Typer(help="Arsenal — 원클릭 데이터 스택. init → run → query.")

_RECIPES_DIRNAME = "recipes"


def _fatal(e: ArsenalError) -> typer.Exit:
    """ArsenalError → `error: {e}` 출력 + exit(1). 커맨드에서는 `raise _fatal(e) from e`로 사용."""
    typer.echo(f"error: {e}", err=True)
    return typer.Exit(1)


def _available_recipes() -> list[str]:
    root = resources.files("arsenal") / _RECIPES_DIRNAME
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _scaffold_recipe(recipe: str, dest: Path) -> None:
    """레시피 패키지 데이터를 dest로 복사. 기존 파일이 하나라도 있으면 통째로 거부."""
    available = _available_recipes()
    if recipe not in available:
        raise FatalError(f"unknown recipe: {recipe!r} (available: {', '.join(available)})")

    src_root = resources.files("arsenal") / _RECIPES_DIRNAME / recipe
    entries = list(_recipe_files(src_root, Path()))

    try:
        for relative, _ in entries:
            if (dest / relative).exists():
                raise FatalError(f"refusing to overwrite existing file: {dest / relative}")
        for relative, entry in entries:
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.read_bytes())
    except OSError as e:
        raise FatalError(f"cannot scaffold recipe into {dest}: {e}") from e


def _recipe_files(root: Traversable, prefix: Path) -> Iterator[tuple[Path, Traversable]]:
    """레시피의 모든 파일을 (상대 경로, 리소스)로 — 하위 디렉터리(input/ 샘플)까지."""
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.is_dir():
            if entry.name != "__pycache__":
                yield from _recipe_files(entry, prefix / entry.name)
        else:
            yield prefix / entry.name, entry


@app.command()
def init(
    recipe: str | None = typer.Argument(None, help="레시피 이름 (예: csv-cleanup)"),
    dest: Path = Path("."),
    list_recipes: bool = typer.Option(False, "--list", help="사용 가능한 레시피 목록 출력"),
) -> None:
    """레시피로 프로젝트 스캐폴드 (예: arsenal init csv-cleanup). --list 로 목록 확인."""
    if list_recipes:
        for name in _available_recipes():
            typer.echo(name)
        return
    try:
        if recipe is None:
            raise FatalError("RECIPE argument required (use --list to see available recipes)")
        _scaffold_recipe(recipe, dest)
    except ArsenalError as e:
        raise _fatal(e) from e
    typer.echo(f"done: {recipe} scaffolded into {dest}")
    typer.echo(f"  1. cd {dest}")
    typer.echo("  2. edit collect.yaml / transform.yaml as needed")
    typer.echo("  3. arsenal run")


@contextlib.contextmanager
def _chdir(path: Path) -> Generator[None, None, None]:
    """상대 경로(sink `./data/...` 등)가 프로젝트 디렉터리 기준으로 풀리게 한다."""
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def _run_pipeline(path: Path) -> None:
    """pugio 파이프라인 하나 실행 — 위임 지점(테스트가 monkeypatch하는 seam)."""
    spec = load_pipeline(path)
    report = _pugio_run_pipeline(spec)
    typer.echo(
        f"  collect {path.name}: "
        f"fetched={report.fetched} written={report.written} skipped={report.skipped}"
    )


def _run_transform(path: Path) -> None:
    """gladius 변환 하나 실행 — 위임 지점(테스트가 monkeypatch하는 seam)."""
    spec = load_transform(path)
    out = gladius_engine.run_transform(spec)
    typer.echo(f"  transform {path.name}: {out}")


def _run_dbt(path: Path) -> None:
    """Run a dbt-duckdb project through dbt's in-process CLI API."""
    if not path.is_dir():
        raise FatalError(f"dbt project not found: {path}")
    try:
        dbt_main: Any = importlib.import_module("dbt.cli.main")
        dbt_runner: Any = dbt_main.dbtRunner
    except (ImportError, AttributeError) as e:
        raise FatalError("dbt stage requires the 'dbt' extra: pip install de-arsenal[dbt]") from e
    try:
        result: Any = dbt_runner().invoke(["run", "--project-dir", str(path)])
    except Exception as e:
        raise FatalError(f"dbt run failed for {path}: {e}") from e
    if not result.success:
        detail = str(getattr(result, "exception", "unknown dbt error"))
        raise FatalError(f"dbt run failed for {path}: {detail}")
    typer.echo(f"  dbt {path}: run succeeded")


def _check_optional_tools(proj: ArsenalProject) -> None:
    """Fail before running anything if a declared stage's extra is missing."""
    if proj.streams:
        stages.require_tool("hasta")
    if proj.distributed:
        stages.require_tool("ballista")


def _run_stream(ref: StreamRef) -> None:
    report = stages.run_stream(ref)
    typer.echo(
        f"  stream {ref.spec.name}: committed={report.get('committed')} "
        f"skipped={report.get('skipped')} exhausted={report.get('exhausted')}"
    )


def _run_distributed(project_name: str, ref: DistributedRef) -> None:
    record = stages.submit_or_reconcile(project_name, ref)
    typer.echo(
        f"  distributed {ref.transform.name}: run={record.get('run_id')} "
        f"status={record.get('status')} (follow with: arsenal status)"
    )


@app.command()
def run(project: Path = Path(".")) -> None:
    """arsenal.yaml 순서대로 수집→스트림→변환→분산 제출. 재실행 = 재개."""
    try:
        proj_dir = project if project.is_absolute() else project.absolute()
        proj = load_project(proj_dir / "arsenal.yaml")
        _check_optional_tools(proj)
        with _chdir(proj_dir):
            for pipeline_path in proj.pipelines:
                _run_pipeline(pipeline_path)
            for stream in proj.streams:
                _run_stream(stream)
            for transform in proj.transforms:
                if isinstance(transform, DbtProjectRef):
                    _run_dbt(transform.dbt)
                else:
                    _run_transform(transform)
            for distributed in proj.distributed:
                _run_distributed(proj.name, distributed)
    except ArsenalError as e:
        raise _fatal(e) from e
    parts = [f"{len(proj.pipelines)} pipeline(s)"]
    if proj.streams:
        parts.append(f"{len(proj.streams)} stream(s)")
    parts.append(f"{len(proj.transforms)} transform(s)")
    if proj.distributed:
        parts.append(f"{len(proj.distributed)} distributed run(s) submitted")
    typer.echo("done: " + ", ".join(parts))


def _check_pipeline(path: Path) -> tuple[str, list[str]]:
    """Load one pipeline spec. Returns (summary, warnings); raises ArsenalError if invalid."""
    spec = load_pipeline(path)
    warnings: list[str] = []
    source = spec.source
    if (
        source.type == "file"
        and not source.allow_empty
        and not glob.glob(source.path, recursive=True)
    ):
        warnings.append(f"source.path '{source.path}' matches no files yet")
    return f"{source.type} -> {spec.sink.type}", warnings


@app.command()
def check(project: Path = Path(".")) -> None:
    """Validate arsenal.yaml and the specs it lists. Runs nothing and writes nothing."""
    proj_dir = project if project.is_absolute() else project.absolute()
    try:
        proj = load_project(proj_dir / "arsenal.yaml")
    except ArsenalError as e:
        raise _fatal(e) from e
    errors = 0
    with _chdir(proj_dir):
        for path in proj.pipelines:
            try:
                summary, warnings = _check_pipeline(path)
            except ArsenalError as e:
                errors += 1
                typer.echo(f"error  {path.name}: {e}", err=True)
                continue
            typer.echo(f"ok     {path.name}: {summary}")
            for warning in warnings:
                typer.echo(f"warn   {path.name}: {warning}")
        for transform in proj.transforms:
            if isinstance(transform, DbtProjectRef):
                typer.echo(f"skip   {transform.dbt.name}: dbt projects are not checked")
                continue
            try:
                compile_sql(load_transform(transform))
            except ArsenalError as e:
                errors += 1
                typer.echo(f"error  {transform.name}: {e}", err=True)
                continue
            typer.echo(f"ok     {transform.name}: compiles")
        for stream in proj.streams:
            typer.echo(f"skip   {stream.spec.name}: stream specs are not checked")
        for distributed in proj.distributed:
            typer.echo(f"skip   {distributed.transform.name}: distributed runs are not checked")
    if errors:
        typer.echo(f"{errors} file(s) with errors", err=True)
        raise typer.Exit(1)
    typer.echo("all specs valid")


@app.command()
def status(project: Path = Path(".")) -> None:
    """Streams and distributed runs in arsenal.yaml: health, checkpoint, run state (JSON)."""
    try:
        proj_dir = project if project.is_absolute() else project.absolute()
        proj = load_project(proj_dir / "arsenal.yaml")
        _check_optional_tools(proj)
        with _chdir(proj_dir):
            streams = [stages.stream_status(ref) for ref in proj.streams]
            distributed = [stages.distributed_status(proj.name, ref) for ref in proj.distributed]
        catalog = stages.catalog_status(proj_dir)
    except ArsenalError as e:
        raise _fatal(e) from e
    alerts = [s for s in streams if stages.needs_attention(s)] + [
        d for d in distributed if stages.needs_attention(d)
    ]
    payload: dict[str, Any] = {
        "project": proj.name,
        "streams": streams,
        "distributed": distributed,
        "alert": bool(alerts) or bool(catalog and catalog["alert"]),
    }
    if catalog is not None:
        payload["catalog"] = catalog
    typer.echo(json.dumps(payload, sort_keys=True, default=str))
    if payload["alert"]:
        raise typer.Exit(1)


@app.command()
def query(sql: str, fmt: str = typer.Option("table", "--format")) -> None:
    """수집·변환 결과에 즉석 SQL (gladius.engine.query 위임)."""
    try:
        result = gladius_engine.query(sql)
    except ArsenalError as e:
        raise _fatal(e) from e

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


# 개별 도구는 서브커맨드로 그대로 노출 — 우산은 감싸되 가리지 않는다
app.add_typer(pugio_app, name="collect", help="수집 (pugio)")
app.add_typer(gladius_app, name="transform", help="변환·쿼리 (gladius)")


if __name__ == "__main__":
    app()
