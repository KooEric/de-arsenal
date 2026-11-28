"""vigil CLI. JSON on stdout, people on stderr. Exit 0 ok, 1 failures, 2 tool error."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from arsenal_core.errors import ArsenalError
from vigil.rule import Rule, load_rules
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
    results = check_rules(_load(rules))
    for rule_id, failures in results.items():
        _err(f"{'FAIL' if failures else 'ok  '} {rule_id}")
        for failure in failures:
            _err(f"     {failure}")
    failed = {rule_id: failures for rule_id, failures in results.items() if failures}
    typer.echo(json.dumps({"rules": len(results), "failed": failed}))
    if failed:
        raise typer.Exit(EXIT_FAILURES)
