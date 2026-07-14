"""Arsenal CLI — 단일 진입점. 위임만 하고 로직을 갖지 않는다.

구현: docs/plans/2026-07-08-m4-integration-release.md Task 4.0
"""

import contextlib
import json
import os
import sys
from collections.abc import Generator
from importlib import resources
from pathlib import Path

import duckdb
import pyarrow.csv as pa_csv
import typer

from arsenal.project import load_project
from arsenal_core.errors import ArsenalError, FatalError
from arsenal_core.spec import load_pipeline
from gladius import engine as gladius_engine
from gladius.cli import app as gladius_app
from gladius.loader import load_transform
from pugio.cli import app as pugio_app
from pugio.runner import run_pipeline as _pugio_run_pipeline

app = typer.Typer(help="Arsenal — 원클릭 데이터 스택. init → run → query.")

_RECIPES_DIRNAME = "recipes"


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

    dest.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        target = dest / entry.name
        if target.exists():
            raise FatalError(f"refusing to overwrite existing file: {target}")
    for entry in entries:
        (dest / entry.name).write_bytes(entry.read_bytes())


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
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
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


@app.command()
def run(project: Path = Path(".")) -> None:
    """arsenal.yaml 순서대로 수집→변환 일괄 실행. 재실행 = 재개."""
    try:
        proj_dir = project if project.is_absolute() else project.absolute()
        proj = load_project(proj_dir / "arsenal.yaml")
        with _chdir(proj_dir):
            for pipeline_path in proj.pipelines:
                _run_pipeline(pipeline_path)
            for transform_path in proj.transforms:
                _run_transform(transform_path)
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"done: {len(proj.pipelines)} pipeline(s), {len(proj.transforms)} transform(s)")


@app.command()
def query(sql: str, fmt: str = typer.Option("table", "--format")) -> None:
    """수집·변환 결과에 즉석 SQL (gladius.engine.query 위임)."""
    try:
        result = gladius_engine.query(sql)
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


# 개별 도구는 서브커맨드로 그대로 노출 — 우산은 감싸되 가리지 않는다
app.add_typer(pugio_app, name="collect", help="수집 (pugio)")
app.add_typer(gladius_app, name="transform", help="변환·쿼리 (gladius)")


if __name__ == "__main__":
    app()
