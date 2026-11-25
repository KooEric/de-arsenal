"""censor CLI. JSON on stdout, people on stderr. Exit 0 clean, 1 failures, 2 tool error."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from arsenal_core.errors import ArsenalError
from censor.audit import audit_project
from censor.history import diff_reports, list_reports, load_report, save_report
from censor.report import Lang, render_markdown

app = typer.Typer(help="Censor — audit a DE Arsenal project and grade it.", no_args_is_help=True)

EXIT_FAILURES = 1
EXIT_TOOL_ERROR = 2


def _err(message: str) -> None:
    typer.echo(message, err=True)


@app.command()
def audit(
    project: Annotated[Path, typer.Argument(help="Project directory")] = Path("."),
    lang: Annotated[str, typer.Option("--lang", help="Report language: en or ko")] = "en",
    json_only: Annotated[bool, typer.Option("--json-only", help="No summary on stderr")] = False,
) -> None:
    """Grade a project (quick tier: reads files only, runs nothing)."""
    if lang not in ("en", "ko"):
        raise typer.BadParameter("use en or ko", param_hint="--lang")
    report_lang: Lang = "ko" if lang == "ko" else "en"
    try:
        result = audit_project(project)
    except ArsenalError as e:
        _err(f"censor: {e}")
        raise typer.Exit(EXIT_TOOL_ERROR) from e
    typer.echo(result.model_dump_json(indent=2))
    if not json_only:
        score = "-" if result.score is None else f"{result.score:.1f}"
        _err(f"grade {result.grade or '-'} · score {score} · run {result.run_id}")
    try:
        save_report(result, project.absolute(), markdown=render_markdown(result, report_lang))
    except OSError as e:
        _err(f"censor: could not save the run under {project}/.arsenal/censor: {e}")
        raise typer.Exit(EXIT_TOOL_ERROR) from e
    if result.has_failures:
        raise typer.Exit(EXIT_FAILURES)


@app.command()
def report(project: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    """List saved runs: run id, grade, score."""
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
