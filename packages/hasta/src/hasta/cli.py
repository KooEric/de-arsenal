"""Hasta foreground stream CLI."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from arsenal_core.errors import ArsenalError
from arsenal_core.state import StateStore
from hasta.loader import load_stream
from hasta.runner import StreamRunner

app = typer.Typer(help="Hasta — leased REST microbatch streaming.")


@app.command(name="run")
def run_cmd(
    spec_path: Path,
    once: bool = typer.Option(False, "--once", help="Process at most one available microbatch."),
    max_batches: int | None = typer.Option(None, "--max-batches", min=1),
) -> None:
    """Run a stream in the foreground, or one deterministic batch with --once."""

    if once and max_batches is not None:
        typer.echo("error: --once and --max-batches cannot be used together", err=True)
        raise typer.Exit(2)
    runner: StreamRunner | None = None
    try:
        runner = StreamRunner(load_stream(spec_path))
        report = (
            runner.run_once()
            if once
            else runner.run(max_batches=max_batches, wait_between_polls=True)
        )
    except (ArsenalError, RuntimeError, ValueError, NotImplementedError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
    finally:
        if runner is not None:
            runner.close()
    typer.echo(json.dumps(report.__dict__, sort_keys=True))


@app.command(name="status")
def status_cmd(spec_path: Path) -> None:
    """Show the durable checkpoint for a stream without polling it."""

    try:
        spec = load_stream(spec_path)
        store = StateStore(Path(spec.state_dir) / f"{spec.name}.db")
        try:
            checkpoint = store.checkpoint(spec.name, spec.stream.name)
        finally:
            store.close()
    except ArsenalError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
    if checkpoint is None:
        typer.echo(json.dumps({"checkpoint": None}))
        return
    typer.echo(
        json.dumps(
            {
                "generation": checkpoint.generation,
                "position": checkpoint.position,
                "watermark": checkpoint.watermark,
                "last_batch_id": checkpoint.last_batch_id,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    app()
