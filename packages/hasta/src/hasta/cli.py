"""Hasta foreground stream CLI."""

from __future__ import annotations

import json
import signal
import threading
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any

import typer

from arsenal_core.errors import ArsenalError
from arsenal_core.state import StateStore
from hasta.loader import load_stream
from hasta.runner import StreamRunner
from hasta.status import state_path, stream_resource, stream_status

app = typer.Typer(help="Hasta — leased REST microbatch streaming.")
checkpoint_app = typer.Typer(help="Inspect or reset a stream's durable checkpoint.")
app.add_typer(checkpoint_app, name="checkpoint")

_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


@contextmanager
def graceful_stop(stop: threading.Event) -> Generator[None, None, None]:
    """First SIGINT/SIGTERM finishes the in-flight batch; a second one aborts."""

    previous: dict[int, Any] = {}

    def handle(signum: int, frame: FrameType | None) -> None:
        del frame
        if stop.is_set():
            raise KeyboardInterrupt
        typer.echo(
            f"received {signal.Signals(signum).name}: finishing the current batch, "
            "then releasing the lease (send again to abort)",
            err=True,
        )
        stop.set()

    if threading.current_thread() is not threading.main_thread():
        yield  # signal handlers can only be installed from the main thread
        return
    for signum in _STOP_SIGNALS:
        previous[signum] = signal.signal(signum, handle)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


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
    stop = threading.Event()
    try:
        runner = StreamRunner(load_stream(spec_path), stop=stop)
        with graceful_stop(stop):
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


def _show(spec_path: Path, max_lag_seconds: float | None) -> None:
    try:
        spec = load_stream(spec_path)
        store = StateStore(state_path(spec))
        try:
            payload = stream_status(spec, store, max_lag_seconds=max_lag_seconds)
        finally:
            store.close()
    except ArsenalError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(payload, sort_keys=True))


_MAX_LAG = typer.Option(
    None,
    "--max-lag-seconds",
    min=1,
    help="Alert when the last commit is older than this (default: 5 polls, min 60 s).",
)


@app.command(name="status")
def status_cmd(spec_path: Path, max_lag_seconds: float | None = _MAX_LAG) -> None:
    """Show health, checkpoint, lease and ledger without polling (= checkpoint show)."""

    _show(spec_path, max_lag_seconds)


@checkpoint_app.command(name="show")
def checkpoint_show(spec_path: Path, max_lag_seconds: float | None = _MAX_LAG) -> None:
    """Show health, checkpoint, lease holder/expiry and batch ledger totals as JSON."""

    _show(spec_path, max_lag_seconds)


@checkpoint_app.command(name="reset")
def checkpoint_reset(
    spec_path: Path,
    confirm: bool = typer.Option(
        False, "--confirm", help="Apply the reset. Without it, only print the plan."
    ),
) -> None:
    """Replay the stream from the source's beginning under a new generation.

    Dry-run by default. Refuses while a live runner holds the lease. Batches
    replayed after a reset get new identities and are written again: a sink
    that appends files (Parquet) will hold both copies unless its output is
    cleared first.
    """

    try:
        spec = load_stream(spec_path)
        store = StateStore(state_path(spec))
        try:
            before = stream_status(spec, store)
            current = before["checkpoint"]
            next_generation = (current["generation"] if current else 0) + 1
            after: dict[str, Any] = {
                "generation": next_generation,
                "position": {},
                "watermark": None,
                "last_batch_id": None,
            }
            plan: dict[str, Any] = {
                "action": "reset" if confirm else "dry-run",
                "before": current,
                "after": after,
                "warning": (
                    f"the next run re-reads the source from the beginning and writes every "
                    f"batch again to {before['sink']['type']} '{before['sink']['target']}'; "
                    "clear that output first if the sink does not replace existing data"
                ),
            }
            lease_info = before["lease"]
            if lease_info is not None and not lease_info["expired"]:
                typer.echo(json.dumps({**plan, "refused": "stream lease is held"}, sort_keys=True))
                typer.echo(
                    f"error: stream is running (lease held by {lease_info['owner_id']}); "
                    "stop it first",
                    err=True,
                )
                raise typer.Exit(1)
            if not confirm:
                typer.echo(json.dumps(plan, sort_keys=True))
                typer.echo("dry-run: re-run with --confirm to apply", err=True)
                return
            token = store.acquire_lease(stream_resource(spec), f"hasta-reset-{uuid.uuid4()}", 30)
            if token is None:
                typer.echo("error: stream lease was taken while resetting", err=True)
                raise typer.Exit(1)
            try:
                reset = store.reset_checkpoint(spec.name, spec.stream.name, lease=token)
            finally:
                store.release_lease(token)
        finally:
            store.close()
    except ArsenalError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps({**plan, "after": {**after, "generation": reset.generation}}))


if __name__ == "__main__":  # pragma: no cover
    app()
