"""vigil CLI. JSON on stdout, people on stderr. Exit 0 ok, 1 failures, 2 tool error."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from arsenal_core.errors import ArsenalError
from vigil.events import load_events
from vigil.rule import SEVERITY_ORDER, Rule, load_rules
from vigil.run import run_rules
from vigil.testing import check_rules

app = typer.Typer(
    help="Vigil — detection rules over normalized security logs.", no_args_is_help=True
)

EXIT_FAILURES = 1
EXIT_TOOL_ERROR = 2

RulesOption = Annotated[Path, typer.Option("--rules", help="Directory of rule YAML files.")]


@app.callback()
def main() -> None:
    """Vigil — detection rules over normalized security logs."""


def _err(message: str) -> None:
    typer.echo(message, err=True)


def _fail(error: ArsenalError) -> typer.Exit:
    _err(f"vigil: {error}")
    return typer.Exit(EXIT_TOOL_ERROR)


def _load(rules_dir: Path) -> list[Rule]:
    try:
        return load_rules(rules_dir)
    except ArsenalError as e:
        raise _fail(e) from e


@app.command("test")
def test_command(rules: RulesOption = Path("rules")) -> None:
    """Check every rule against its fixture cases."""
    loaded = _load(rules)
    try:
        results = check_rules(loaded)
    except ArsenalError as e:
        raise _fail(e) from e
    for rule_id, failures in results.items():
        _err(f"{'FAIL' if failures else 'ok  '} {rule_id}")
        for failure in failures:
            _err(f"     {failure}")
    failed = {rule_id: failures for rule_id, failures in results.items() if failures}
    typer.echo(json.dumps({"rules": len(results), "failed": failed}))
    if failed:
        raise typer.Exit(EXIT_FAILURES)


class FailOn(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@app.command("run")
def run_command(
    events: Annotated[str, typer.Option("--events", help="Glob or directory of events Parquet.")],
    out: Annotated[Path, typer.Option("--out", help="Directory alerts are appended to.")],
    rules: RulesOption = Path("rules"),
    fail_on: Annotated[
        FailOn | None, typer.Option("--fail-on", help="Exit 1 on a new alert at or above this.")
    ] = None,
) -> None:
    """Run every rule over the events lake and record new alerts."""
    loaded = _load(rules)
    try:
        con = load_events(events)
    except ArsenalError as e:
        raise _fail(e) from e
    try:
        summary = run_rules(loaded, con, out, datetime.now(UTC).replace(tzinfo=None))
    finally:
        con.close()
    for rule_id, count in summary.new_by_rule.items():
        _err(f"{count:>5} new  {rule_id}")
    for rule_id, reason in summary.failed.items():
        _err(f"ERROR      {rule_id}: {reason}")
    typer.echo(
        json.dumps(
            {
                "rules": len(loaded),
                "new_alerts": sum(summary.new_by_rule.values()),
                "by_rule": summary.new_by_rule,
                "failed": summary.failed,
            }
        )
    )
    rank = {name: position for position, name in enumerate(SEVERITY_ORDER)}
    over = fail_on is not None and any(
        rank[severity] >= rank[fail_on.value] for severity in summary.new_severities
    )
    if summary.failed or over:
        raise typer.Exit(EXIT_FAILURES)
