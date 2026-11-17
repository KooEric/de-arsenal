"""Operational UX: checkpoint show/reset, graceful stop, lease visibility."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
import yaml
from hasta.cli import app, graceful_stop
from hasta.model import StreamPosition, batch_identity
from hasta.runner import StreamRunner
from hasta.spec import StreamSpec
from typer.testing import CliRunner

from arsenal_core.state import StateStore


def _helpers() -> Any:
    path = Path(__file__).with_name("test_stream.py")
    spec = importlib.util.spec_from_file_location("hasta_tests_stream_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _helpers()
cli = CliRunner()


def write_spec(tmp_path: Path) -> Path:
    path = tmp_path / "stream.yaml"
    path.write_text(
        yaml.safe_dump(H.spec(tmp_path).model_dump(mode="json", exclude_none=True, by_alias=True)),
        encoding="utf-8",
    )
    return path


def state(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state" / "orders.db")


def run_one_batch(tmp_path: Path, store: StateStore, sink: Any = None) -> Any:
    runner = StreamRunner(
        H.spec(tmp_path),
        source=H.Source(pa.table({"id": ["a", "b"]})),
        sink=sink or H.IdempotentSink(),
        store=store,
    )
    return runner.run_once()


# --- generation-aware identity -------------------------------------------------


def test_generation_zero_keeps_existing_batch_ids() -> None:
    args: dict[str, Any] = {
        "pipeline": "p",
        "stream_name": "s",
        "revision": "r",
        "source_id": "src",
        "start": StreamPosition({}),
        "end": StreamPosition({"cursor": "c"}),
        "event_hash": "e",
    }
    assert batch_identity(**args) == batch_identity(**args, generation=0)
    assert batch_identity(**args) != batch_identity(**args, generation=1)


def test_reset_replays_from_the_beginning_with_new_batch_ids(tmp_path: Path) -> None:
    store = state(tmp_path)
    sink = H.IdempotentSink()
    run_one_batch(tmp_path, store, sink)
    lease = store.acquire_lease("stream:orders:orders", "reset", 30)
    assert lease is not None
    store.reset_checkpoint("orders", "orders", lease=lease)
    store.release_lease(lease)

    source = H.Source(pa.table({"id": ["a", "b"]}))
    report = StreamRunner(H.spec(tmp_path), source=source, sink=sink, store=store).run_once()

    assert source.after == [None]  # reset position means "from the beginning"
    assert report.committed == 1 and report.skipped == 0
    assert len(set(sink.writes)) == 2  # replay wrote under a new identity
    assert store.checkpoint("orders", "orders").generation == 1  # type: ignore[union-attr]


def test_without_reset_a_rerun_is_skipped_as_committed(tmp_path: Path) -> None:
    store = state(tmp_path)
    sink = H.IdempotentSink()
    run_one_batch(tmp_path, store, sink)

    report = StreamRunner(
        H.spec(tmp_path),
        source=H.Source(pa.table({"id": ["a", "b"]})),
        sink=sink,
        store=store,
    ).run_once()

    assert report.polls == 0 and report.exhausted  # exhausted checkpoint short-circuits
    assert len(sink.writes) == 1


# --- graceful stop --------------------------------------------------------------


def test_stop_before_start_polls_nothing_and_releases_lease(tmp_path: Path) -> None:
    store = state(tmp_path)
    stop = threading.Event()
    stop.set()
    runner = StreamRunner(
        H.spec(tmp_path),
        source=H.Source(pa.table({"id": ["a"]})),
        sink=H.IdempotentSink(),
        store=store,
        stop=stop,
    )

    report = runner.run(max_batches=5)

    assert report.stopped is True and report.polls == 0
    assert store.lease("stream:orders:orders") is None


def test_stop_during_poll_wait_finishes_current_batch_then_exits(tmp_path: Path) -> None:
    store = state(tmp_path)
    source = H.Source(pa.table({"id": ["a"]}), exhausted=False)
    runner: StreamRunner | None = None

    def stop_while_sleeping(seconds: float) -> None:
        assert runner is not None
        runner.stop()

    runner = StreamRunner(
        H.spec(tmp_path),
        source=source,
        sink=H.IdempotentSink(),
        store=store,
        sleep=stop_while_sleeping,
    )

    report = runner.run(wait_between_polls=True)

    assert report.stopped is True
    assert report.polls == 1 and report.committed == 1
    assert store.checkpoint("orders", "orders") is not None
    assert store.lease("stream:orders:orders") is None


def test_default_poll_wait_ends_as_soon_as_stop_is_requested(tmp_path: Path) -> None:
    long_wait = H.spec(tmp_path).model_dump(mode="json", exclude_none=True, by_alias=True)
    long_wait["stream"]["poll_interval_seconds"] = 30
    runner = StreamRunner(
        StreamSpec.model_validate(long_wait),
        source=H.Source(pa.table({"id": ["a"]}), exhausted=False),
        sink=H.IdempotentSink(),
        store=state(tmp_path),
    )
    timer = threading.Timer(0.2, runner.stop)
    timer.start()
    started = time.monotonic()

    report = runner.run(wait_between_polls=True)

    assert report.stopped is True
    assert time.monotonic() - started < 10  # did not sit out the 30 s interval


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_first_signal_requests_stop_and_second_aborts() -> None:
    stop = threading.Event()
    with graceful_stop(stop):
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop.is_set()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL or callable(
        signal.getsignal(signal.SIGTERM)
    )


# --- checkpoint show / reset CLI ---------------------------------------------------


def test_checkpoint_show_reports_checkpoint_lease_and_ledger(tmp_path: Path) -> None:
    path = write_spec(tmp_path)
    store = state(tmp_path)
    run_one_batch(tmp_path, store)
    store.acquire_lease("stream:orders:orders", "live-runner", 60)
    store.close()

    result = cli.invoke(app, ["checkpoint", "show", str(path)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["checkpoint"]["generation"] == 0
    assert payload["checkpoint"]["position"]["cursor"] == "done"
    assert payload["lease"]["owner_id"] == "live-runner"
    assert payload["lease"]["expired"] is False
    assert payload["batches"]["by_status"] == {"committed": 1}
    assert payload["batches"]["rows"] == 2
    assert payload["sink"] == {"type": "parquet", "target": str(tmp_path / "out")}


def test_status_is_an_alias_of_checkpoint_show(tmp_path: Path) -> None:
    path = write_spec(tmp_path)

    show = cli.invoke(app, ["checkpoint", "show", str(path)])
    status = cli.invoke(app, ["status", str(path)])

    assert json.loads(show.stdout) == json.loads(status.stdout)
    assert json.loads(status.stdout)["checkpoint"] is None


def test_reset_is_a_dry_run_without_confirm(tmp_path: Path) -> None:
    path = write_spec(tmp_path)
    store = state(tmp_path)
    run_one_batch(tmp_path, store)
    store.close()

    result = cli.invoke(app, ["checkpoint", "reset", str(path)])

    assert result.exit_code == 0
    plan = json.loads(result.stdout)
    assert plan["action"] == "dry-run"
    assert plan["after"]["generation"] == 1
    assert str(tmp_path / "out") in plan["warning"]
    store = state(tmp_path)
    assert store.checkpoint("orders", "orders").generation == 0  # type: ignore[union-attr]


def test_reset_with_confirm_bumps_generation_and_frees_the_lease(tmp_path: Path) -> None:
    path = write_spec(tmp_path)
    store = state(tmp_path)
    run_one_batch(tmp_path, store)
    store.close()

    result = cli.invoke(app, ["checkpoint", "reset", str(path), "--confirm"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["after"]["generation"] == 1
    store = state(tmp_path)
    checkpoint = store.checkpoint("orders", "orders")
    assert checkpoint is not None and checkpoint.position == {}
    assert store.lease("stream:orders:orders") is None


def test_reset_refuses_while_a_runner_holds_the_lease(tmp_path: Path) -> None:
    path = write_spec(tmp_path)
    store = state(tmp_path)
    run_one_batch(tmp_path, store)
    store.acquire_lease("stream:orders:orders", "live-runner", 60)
    store.close()

    result = cli.invoke(app, ["checkpoint", "reset", str(path), "--confirm"])

    assert result.exit_code == 1
    assert "live-runner" in result.output
    store = state(tmp_path)
    assert store.checkpoint("orders", "orders").generation == 0  # type: ignore[union-attr]


# --- health (Scorpio) -------------------------------------------------------------


def test_show_reports_healthy_after_a_fresh_commit(tmp_path: Path) -> None:
    path = write_spec(tmp_path)
    store = state(tmp_path)
    run_one_batch(tmp_path, store)
    store.close()

    payload = json.loads(cli.invoke(app, ["checkpoint", "show", str(path)]).stdout)

    assert payload["health"]["status"] == "healthy"
    assert payload["health"]["max_lag_seconds"] == 60
    assert payload["checkpoint"]["updated_at"] is not None


def test_show_flags_quarantined_batches_as_failing(tmp_path: Path) -> None:
    path = write_spec(tmp_path)
    store = state(tmp_path)
    run_one_batch(tmp_path, store)
    store.close()
    dlq = tmp_path / "state" / "dlq" / "orders"
    dlq.mkdir(parents=True)
    (dlq / "batch-x.json").write_text("{}", encoding="utf-8")

    payload = json.loads(cli.invoke(app, ["checkpoint", "show", str(path)]).stdout)

    assert payload["batches"]["dlq"] == 1
    assert payload["health"]["status"] == "failing"
    assert payload["health"]["alert"] is True


def test_show_flags_a_dead_runner_holding_an_expired_lease(tmp_path: Path) -> None:
    path = write_spec(tmp_path)
    store = state(tmp_path)
    run_one_batch(tmp_path, store)
    store.acquire_lease("stream:orders:orders", "dead", 0.001)
    store.close()
    time.sleep(0.01)

    payload = json.loads(cli.invoke(app, ["checkpoint", "show", str(path)]).stdout)

    assert payload["lease"]["expired"] is True
    assert payload["health"]["status"] == "stalled"


def test_postgres_sink_target_shows_env_var_name_not_dsn(tmp_path: Path) -> None:
    from hasta.status import sink_target

    data = H.spec(tmp_path).model_dump(mode="json", exclude_none=True, by_alias=True)
    data["sink"] = {"type": "postgres", "dsn_env": "ORDERS_DSN", "table": "t", "merge_key": ["id"]}

    assert sink_target(StreamSpec.model_validate(data)) == "$ORDERS_DSN#t"
