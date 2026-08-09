"""Arsenal CLI — 단일 진입점. 위임만 하고 로직을 갖지 않는다.

구현: docs/plans/2026-07-08-m4-integration-release.md Task 4.0
"""

import contextlib
import importlib
import json
import os
import sys
from collections.abc import Generator
from importlib import resources
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.csv as pa_csv
import typer

from arsenal.project import DbtProjectRef, load_project
from arsenal_core.errors import ArsenalError, FatalError
from arsenal_core.spec import load_pipeline
from gladius import engine as gladius_engine
from gladius.cli import app as gladius_app
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
    entries = [p for p in src_root.iterdir() if p.is_file()]

    try:
        dest.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            target = dest / entry.name
            if target.exists():
                raise FatalError(f"refusing to overwrite existing file: {target}")
        for entry in entries:
            (dest / entry.name).write_bytes(entry.read_bytes())
    except OSError as e:
        raise FatalError(f"cannot scaffold recipe into {dest}: {e}") from e


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


@app.command()
def run(project: Path = Path(".")) -> None:
    """arsenal.yaml 순서대로 수집→변환 일괄 실행. 재실행 = 재개."""
    try:
        proj_dir = project if project.is_absolute() else project.absolute()
        proj = load_project(proj_dir / "arsenal.yaml")
        with _chdir(proj_dir):
            for pipeline_path in proj.pipelines:
                _run_pipeline(pipeline_path)
            for transform in proj.transforms:
                if isinstance(transform, DbtProjectRef):
                    _run_dbt(transform.dbt)
                else:
                    _run_transform(transform)
    except ArsenalError as e:
        raise _fatal(e) from e
    typer.echo(f"done: {len(proj.pipelines)} pipeline(s), {len(proj.transforms)} transform(s)")


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
