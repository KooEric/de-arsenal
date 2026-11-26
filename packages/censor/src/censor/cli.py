"""censor CLI. JSON on stdout, people on stderr. Exit 0 clean, 1 failures, 2 tool error."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final

import typer

from arsenal_core.errors import ArsenalError, FatalError
from censor.audit import AuditRequest, run_audit
from censor.history import diff_reports, list_reports, load_report, save_report, unique_run_id
from censor.report import Lang, render_markdown
from censor.target import (
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_TIMEOUT_SECONDS,
    DataTarget,
    DuckDBTarget,
    FilesTarget,
    PostgresTarget,
    ScanOptions,
)

app = typer.Typer(
    help="Censor — audit a DE Arsenal project or dataset and grade it.", no_args_is_help=True
)

EXIT_FAILURES = 1
EXIT_TOOL_ERROR = 2


class Tier(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"


# What `censor audit DIR` runs when --tier is not given.
DEFAULT_PROJECT_TIER: Final = Tier.QUICK


def _err(message: str) -> None:
    typer.echo(message, err=True)


def _fail(message: str) -> typer.Exit:
    _err(f"censor: {message}")
    return typer.Exit(EXIT_TOOL_ERROR)


def _data_target(
    files: str | None, duckdb_path: Path | None, table: str | None, dsn_env: str | None
) -> DataTarget | None:
    if files is not None and (duckdb_path or table or dsn_env) or (duckdb_path and dsn_env):
        raise FatalError(
            "give one data target: --files, or --duckdb with --table, "
            "or --table with --postgres-dsn-env"
        )
    if files is not None:
        return FilesTarget(files)
    if duckdb_path is not None:
        if table is None:
            raise FatalError("--duckdb needs --table")
        return DuckDBTarget(duckdb_path.absolute(), table)
    if dsn_env is not None:
        if table is None:
            raise FatalError("--postgres-dsn-env needs --table")
        return PostgresTarget(table, dsn_env)
    if table is not None:
        raise FatalError("--table needs --duckdb or --postgres-dsn-env")
    return None


def _sample(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip().removesuffix("%"))
    except ValueError:
        raise FatalError(f"invalid --sample {value!r}: use a percentage such as 1%") from None


def _since(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FatalError(f"invalid --since {value!r}: use a date such as 2026-11-01") from None


def _keys(values: list[str] | None) -> tuple[str, ...]:
    parts = (part.strip() for value in values or [] for part in value.split(","))
    return tuple(part for part in parts if part)


@app.command()
def audit(
    project: Annotated[
        Path, typer.Argument(help="Project directory (with a data target: where history goes)")
    ] = Path("."),
    lang: Annotated[str, typer.Option("--lang", help="Report language: en or ko")] = "en",
    json_only: Annotated[bool, typer.Option("--json-only", help="No summary on stderr")] = False,
    tier: Annotated[
        Tier | None,
        typer.Option(
            "--tier",
            help="quick reads project files only; standard also reads run state and data "
            f"[default: {DEFAULT_PROJECT_TIER.value}; standard for a data target]",
        ),
    ] = None,
    files: Annotated[
        str | None, typer.Option("--files", help="Data target: a glob of Parquet/CSV/JSONL files")
    ] = None,
    duckdb_path: Annotated[
        Path | None, typer.Option("--duckdb", help="Data target: a DuckDB file (with --table)")
    ] = None,
    table: Annotated[
        str | None, typer.Option("--table", help="Table in --duckdb, or in Postgres")
    ] = None,
    postgres_dsn_env: Annotated[
        str | None,
        typer.Option("--postgres-dsn-env", help="Name of the variable holding the Postgres DSN"),
    ] = None,
    key: Annotated[
        list[str] | None, typer.Option("--key", help="Key column(s); repeat or comma-separate")
    ] = None,
    time_column: Annotated[
        str | None, typer.Option("--time-column", help="Column that dates each row")
    ] = None,
    max_age_hours: Annotated[
        float | None, typer.Option("--max-age-hours", help="Older than this is not fresh")
    ] = None,
    sample: Annotated[
        str | None, typer.Option("--sample", help="Profile a sample only, e.g. 1%")
    ] = None,
    since: Annotated[
        str | None, typer.Option("--since", help="Only rows from this date (needs --time-column)")
    ] = None,
    full: Annotated[
        bool, typer.Option("--full", help="Read everything, even over the scale limits")
    ] = False,
    memory_limit: Annotated[
        str, typer.Option("--memory-limit", help="DuckDB memory limit; more spills to disk")
    ] = DEFAULT_MEMORY_LIMIT,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Seconds allowed per query")
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Grade a project, or a bare dataset given with --files, --duckdb or --postgres-dsn-env.

    Nothing is run and nothing is changed: Censor only reads, and writes its history under
    .arsenal/censor/.
    """
    if lang not in ("en", "ko"):
        raise typer.BadParameter("use en or ko", param_hint="--lang")
    report_lang: Lang = "ko" if lang == "ko" else "en"
    base = project.absolute()
    try:
        data = _data_target(files, duckdb_path, table, postgres_dsn_env)
        if data is not None and not base.is_dir():
            raise FatalError(f"no such directory: {project}")
        chosen = tier or (Tier.STANDARD if data is not None else DEFAULT_PROJECT_TIER)
        given = {
            "--key": key,
            "--time-column": time_column,
            "--sample": sample,
            "--since": since,
            "--full": full or None,
        }
        unused = [name for name, value in given.items() if value is not None]
        if chosen is Tier.QUICK and unused:
            raise FatalError(
                f"{', '.join(unused)} needs --tier standard: the quick tier reads no data"
            )
        request = AuditRequest(
            root=base,
            tier="quick" if chosen is Tier.QUICK else "standard",
            data=data,
            key=_keys(key),
            time_column=time_column,
            max_age_hours=max_age_hours,
            scan=ScanOptions(
                sample_percent=_sample(sample),
                since=_since(since),
                full=full,
                memory_limit=memory_limit,
                timeout_seconds=timeout,
            ),
        )
        outcome = run_audit(request)
    except ArsenalError as e:
        raise _fail(str(e)) from e
    run_id = unique_run_id(base, outcome.report.run_id)
    result = outcome.report.model_copy(update={"run_id": run_id})
    typer.echo(result.model_dump_json(indent=2))
    if not json_only:
        score = "-" if result.score is None else f"{result.score:.1f}"
        _err(f"grade {result.grade or '-'} · score {score} · run {result.run_id}")
        for notice in result.notices:
            _err(f"\n{notice}")
    try:
        save_report(result, base, markdown=render_markdown(result, report_lang))
    except OSError as e:
        raise _fail(f"could not save the run under {project}/.arsenal/censor: {e}") from e
    if outcome.tool_errors:
        for message in outcome.tool_errors:
            _err(f"censor: {message}")
        raise typer.Exit(EXIT_TOOL_ERROR)
    if result.has_failures:
        raise typer.Exit(EXIT_FAILURES)


@app.command()
def report(project: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    """List saved runs: run id, grade, score."""
    if not project.is_dir():
        raise _fail(f"no such directory: {project}")
    listing = list_reports(project.absolute())
    for path in listing.skipped:
        _err(f"censor: skipped unreadable saved run {path}")
    for saved in listing.reports:
        score = "-" if saved.score is None else f"{saved.score:.1f}"
        typer.echo(f"{saved.run_id}\t{saved.grade or '-'}\t{score}")


@app.command()
def compare(
    run_a: str,
    run_b: str,
    project: Annotated[Path, typer.Option("--project")] = Path("."),
) -> None:
    """Show the checks whose status changed between two saved runs."""
    try:
        before = load_report(project.absolute(), run_a)
        after = load_report(project.absolute(), run_b)
    except ArsenalError as e:
        _err(f"censor: {e}")
        raise typer.Exit(EXIT_TOOL_ERROR) from e
    for change in diff_reports(before, after):
        old = change.before.value if change.before else "absent"
        new = change.after.value if change.after else "absent"
        typer.echo(f"{change.id}: {old} -> {new}")


def run_cli(argv: Sequence[str]) -> int:
    """Run the CLI in-process and return its exit code (used by `arsenal eval`)."""
    command = typer.main.get_command(app)
    # Standalone mode prints usage errors and always ends in SystemExit. Catching that avoids
    # Typer's vendored Click exception classes, which are not public API (typer 0.26).
    try:
        command.main(args=list(argv), prog_name="censor", standalone_mode=True)
    except SystemExit as e:
        if e.code is None:
            return 0
        return e.code if isinstance(e.code, int) else EXIT_TOOL_ERROR
    return 0
