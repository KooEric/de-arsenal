"""Pilum CLI: plan, run, status, replay, reset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from arsenal_core.errors import ArsenalError
from pilum.ledger import Ledger, RunLock, ledger_path
from pilum.loader import load_dispatch
from pilum.runner import dispatch_status, plan_dispatch, run_dispatch

app = typer.Typer(help="Pilum (experimental) — send changed records to HTTP endpoints and Slack.")

_SAMPLE = 3


def _fail(error: Exception) -> typer.Exit:
    typer.echo(f"error: {error}", err=True)
    return typer.Exit(1)


@app.command(name="plan")
def plan_cmd(spec_path: Path) -> None:
    """Show what `run` would send. Sends nothing and writes nothing."""

    try:
        spec = load_dispatch(spec_path)
        plan = plan_dispatch(spec)
    except ArsenalError as error:
        raise _fail(error) from error
    samples: dict[str, list[Any]] = {}
    for action in plan.actions:
        bucket = samples.setdefault(action.kind, [])
        if len(bucket) < _SAMPLE:
            bucket.append(json.loads(action.record_key))
    payload = {
        "dispatch": spec.name,
        "mode": spec.mode,
        "first_dispatch": plan.first_dispatch,
        **plan.summary(),
        "sample_keys": samples,
    }
    typer.echo(json.dumps(payload, sort_keys=True))


@app.command(name="run")
def run_cmd(
    spec_path: Path,
    confirm_initial_load: bool = typer.Option(
        False,
        "--confirm-initial-load",
        help="Allow a first run larger than safety.max_first_dispatch.",
    ),
    confirm_deletes: bool = typer.Option(
        False,
        "--confirm-deletes",
        help="Allow a mirror run to delete more than safety.max_delete_fraction of the ledger.",
    ),
) -> None:
    """Send new, changed and (mirror) removed records. Exits 1 if any were not delivered."""

    try:
        report = run_dispatch(
            load_dispatch(spec_path),
            confirm_initial_load=confirm_initial_load,
            confirm_deletes=confirm_deletes,
        )
    except ArsenalError as error:
        raise _fail(error) from error
    typer.echo(json.dumps(report.to_dict(), sort_keys=True))
    if not report.ok:
        if report.aborted:
            typer.echo(f"error: {report.aborted}", err=True)
        raise typer.Exit(1)


@app.command(name="status")
def status_cmd(spec_path: Path) -> None:
    """Ledger size, last run and dead letters as JSON. Exits 1 when something needs attention."""

    try:
        payload = dispatch_status(load_dispatch(spec_path))
    except ArsenalError as error:
        raise _fail(error) from error
    typer.echo(json.dumps(payload, sort_keys=True))
    if payload["alert"]:
        raise typer.Exit(1)


@app.command(name="replay")
def replay_cmd(
    spec_path: Path,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Requeue at most N."),
) -> None:
    """Put dead letters back in line. The next `run` sends their current version."""

    try:
        spec = load_dispatch(spec_path)
        path = ledger_path(spec.state_dir, spec.name)
        with RunLock(path):
            ledger = Ledger(path)
            try:
                requeued = ledger.requeue(limit)
            finally:
                ledger.close()
    except ArsenalError as error:
        raise _fail(error) from error
    typer.echo(json.dumps({"dispatch": spec.name, "requeued": requeued}, sort_keys=True))


@app.command(name="reset")
def reset_cmd(
    spec_path: Path,
    confirm: bool = typer.Option(False, "--confirm", help="Apply. Without it, only describe."),
) -> None:
    """Forget everything that was sent. The next run sends every record again."""

    try:
        spec = load_dispatch(spec_path)
        path = ledger_path(spec.state_dir, spec.name)
        with RunLock(path):
            ledger = Ledger(path)
            try:
                size = ledger.size()
                if confirm:
                    ledger.reset()
            finally:
                ledger.close()
    except ArsenalError as error:
        raise _fail(error) from error
    payload = {
        "dispatch": spec.name,
        "action": "reset" if confirm else "dry-run",
        "forgotten_records": size,
        "warning": f"the next run re-sends all source records to {spec.destination.type}",
    }
    typer.echo(json.dumps(payload, sort_keys=True))
    if not confirm:
        typer.echo("dry-run: re-run with --confirm to apply", err=True)


if __name__ == "__main__":  # pragma: no cover
    app()
