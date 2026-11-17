"""Optional stages: Hasta streams and Ballista submissions inside `arsenal run`/`status`."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
import yaml
from arsenal import stages
from arsenal.cli import app
from arsenal.project import DistributedRef, StreamRef, load_project
from typer.testing import CliRunner

from arsenal_core.errors import FatalError
from arsenal_core.state import StateStore

runner = CliRunner()

FULL_MANIFEST = """\
name: lakehouse
pipelines: [collect.yaml]
streams:
  - spec: streams/orders.yaml
    max_batches: 5
transforms: [transform.yaml]
distributed:
  - transform: big.yaml
    profile: profiles/spark.yaml
    input_version: "42"
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_manifest_resolves_stream_and_distributed_paths(tmp_path: Path) -> None:
    write(tmp_path / "arsenal.yaml", FULL_MANIFEST)

    project = load_project(tmp_path / "arsenal.yaml")

    assert project.streams[0].spec == tmp_path / "streams" / "orders.yaml"
    assert project.streams[0].max_batches == 5
    ref = project.distributed[0]
    assert (ref.transform, ref.profile) == (tmp_path / "big.yaml", tmp_path / "profiles/spark.yaml")
    assert ref.state_dir == tmp_path / ".arsenal"


def test_run_orders_all_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "arsenal.yaml", FULL_MANIFEST)
    calls: list[str] = []

    def collect(path: Path) -> None:
        calls.append(f"collect:{path.name}")

    def stream(ref: StreamRef) -> None:
        calls.append(f"stream:{ref.spec.name}")

    def transform(path: Path) -> None:
        calls.append(f"transform:{path.name}")

    def distributed(_project: str, ref: DistributedRef) -> None:
        calls.append(f"distributed:{ref.transform.name}")

    monkeypatch.setattr("arsenal.cli._run_pipeline", collect)
    monkeypatch.setattr("arsenal.cli._run_stream", stream)
    monkeypatch.setattr("arsenal.cli._run_transform", transform)
    monkeypatch.setattr("arsenal.cli._run_distributed", distributed)

    result = runner.invoke(app, ["run", "--project", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls == [
        "collect:collect.yaml",
        "stream:orders.yaml",
        "transform:transform.yaml",
        "distributed:big.yaml",
    ]
    assert "1 stream(s)" in result.output and "1 distributed run(s)" in result.output


@pytest.mark.parametrize(("module", "extra"), [("hasta", "streaming"), ("ballista", "distributed")])
def test_missing_extra_names_the_install_command_before_running_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str, extra: str
) -> None:
    write(tmp_path / "arsenal.yaml", FULL_MANIFEST)
    real_find_spec = importlib.util.find_spec

    def find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        return None if name == module else real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    ran: list[str] = []

    def collect(path: Path) -> None:
        ran.append(path.name)

    monkeypatch.setattr("arsenal.cli._run_pipeline", collect)

    result = runner.invoke(app, ["run", "--project", str(tmp_path)])

    assert result.exit_code == 1
    assert f"de-arsenal[{extra}]" in result.output
    assert ran == []  # nothing half-ran


def ref(tmp_path: Path, version: str = "42") -> DistributedRef:
    return DistributedRef(
        transform=tmp_path / "big.yaml",
        profile=tmp_path / "p.yaml",
        input_version=version,
        state_dir=tmp_path / ".arsenal",
    )


def test_distributed_run_id_is_stable_per_input_and_moves_with_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first = stages.distributed_run_id("lakehouse", ref(tmp_path))

    assert first == stages.distributed_run_id("lakehouse", ref(tmp_path))
    assert first != stages.distributed_run_id("lakehouse", ref(tmp_path, "43"))
    moved = tmp_path / "moved"
    moved.mkdir()
    monkeypatch.chdir(moved)
    assert stages.distributed_run_id("lakehouse", ref(moved)) == first


def _tool_present(_module: str) -> None:
    return None


class FakeBallista:
    def __init__(self, *, existing: bool) -> None:
        self.existing = existing
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        command = argv[3:]
        self.commands.append(command)
        if command[0] == "status" and not self.existing:
            return subprocess.CompletedProcess(argv, 1, "", "error: unknown Ballista run")
        status = {"status": "running", "run": "submitted", "reconcile": "succeeded"}[command[0]]
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"run_id": "x", "status": status}), ""
        )


def test_first_run_submits_and_rerun_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(stages, "require_tool", _tool_present)

    fresh = FakeBallista(existing=False)
    monkeypatch.setattr(subprocess, "run", fresh)
    assert stages.submit_or_reconcile("lakehouse", ref(tmp_path))["status"] == "submitted"
    run_cmd = fresh.commands[1]
    assert run_cmd[0] == "run" and run_cmd[run_cmd.index("--run-id") + 1].startswith("arsenal-")

    again = FakeBallista(existing=True)
    monkeypatch.setattr(subprocess, "run", again)
    assert stages.submit_or_reconcile("lakehouse", ref(tmp_path))["status"] == "succeeded"
    assert [c[0] for c in again.commands] == ["status", "reconcile"]


def test_failed_submission_surfaces_the_last_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stages, "require_tool", _tool_present)

    def failing(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "trace\nerror: preflight failed")

    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(FatalError, match="preflight failed"):
        stages.submit_or_reconcile("lakehouse", ref(tmp_path))


# --- arsenal status over real Hasta state --------------------------------------------


def stream_project(tmp_path: Path) -> Path:
    spec = {
        "name": "orders",
        "state_dir": str(tmp_path / "state"),
        "source": {
            "type": "rest_poll",
            "url": "https://api.example.test/orders",
            "cursor": {"request_param": "after", "response_path": "next", "event_id_path": "id"},
        },
        "stream": {
            "name": "orders",
            "poll_interval_seconds": 1,
            "batch": {"max_records": 10, "max_bytes": 100000, "max_wait_seconds": 1},
            "checkpoint": {"lease_ttl_seconds": 30},
        },
        "sink": {"type": "parquet", "path": str(tmp_path / "out")},
    }
    write(tmp_path / "streams" / "orders.yaml", yaml.safe_dump(spec))
    write(
        tmp_path / "arsenal.yaml",
        "name: lakehouse\nstreams:\n  - spec: streams/orders.yaml\n"
        "distributed:\n  - {transform: big.yaml, profile: p.yaml, input_version: '42'}\n",
    )
    return tmp_path


def commit_one_batch(tmp_path: Path) -> None:
    from hasta.loader import load_stream
    from hasta.model import MicroBatch, StreamPosition
    from hasta.runner import StreamRunner

    class Source:
        source_id = "s"

        def poll(self, after: Any, limits: Any) -> MicroBatch:
            end = StreamPosition({"cursor": "1"})
            return MicroBatch(after or StreamPosition({}), end, pa.table({"id": [1]}), "h", True)

        def close(self) -> None:
            pass

    runner_ = StreamRunner(load_stream(tmp_path / "streams" / "orders.yaml"), source=Source())
    try:
        runner_.run_once()
    finally:
        runner_.close()


def test_status_reports_stream_health_and_unsubmitted_run(tmp_path: Path) -> None:
    project = stream_project(tmp_path)
    commit_one_batch(tmp_path)

    result = runner.invoke(app, ["status", "--project", str(project)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["streams"][0]["health"]["status"] == "healthy"
    assert payload["streams"][0]["batches"]["rows"] == 1
    assert payload["distributed"][0]["status"] == "not-submitted"
    assert payload["alert"] is False


def test_status_exits_nonzero_when_something_needs_attention(tmp_path: Path) -> None:
    project = stream_project(tmp_path)
    commit_one_batch(tmp_path)
    write(tmp_path / "state" / "dlq" / "orders" / "bad.json", "{}")

    result = runner.invoke(app, ["status", "--project", str(project)])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["alert"] is True


def test_status_summarizes_recorded_ballista_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    run_id = stages.distributed_run_id("lakehouse", ref(tmp_path))
    store = StateStore(tmp_path / ".arsenal" / "ballista.sqlite")
    store.create_run(
        run_id=run_id,
        pipeline="big",
        revision="r",
        mode="transform",
        executor="spark_kubernetes",
        guarantees={},
    )
    store.close()

    status = stages.distributed_status("lakehouse", ref(tmp_path))

    assert status["status"] == "planned"
    assert status["history"]["total"] == 1


# --- catalog summary (Aquila) ---------------------------------------------------------


def test_status_includes_the_catalog_and_alerts_on_contract_findings(tmp_path: Path) -> None:
    from aquila import service
    from aquila.store import CatalogStore

    project = stream_project(tmp_path)
    commit_one_batch(tmp_path)
    write(
        tmp_path / "collect.yaml",
        yaml.safe_dump(
            {
                "name": "orders",
                "source": {"type": "file", "path": "./raw/*.csv"},
                "sink": {"type": "parquet", "path": str(tmp_path / "out")},
                "contract": {"columns": [{"name": "total", "type": "double"}]},
            }
        ),
    )
    store = CatalogStore(tmp_path / ".arsenal" / "aquila.db")
    service.scan(store, [tmp_path], root=tmp_path)
    store.close()

    result = runner.invoke(app, ["status", "--project", str(project)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1 and payload["alert"] is True
    assert payload["catalog"]["totals"]["datasets"] >= 2
    assert payload["catalog"]["contract_findings"][0].startswith("orders: total: missing")


def test_status_without_a_catalog_has_no_catalog_block(tmp_path: Path) -> None:
    project = stream_project(tmp_path)
    commit_one_batch(tmp_path)

    payload = json.loads(runner.invoke(app, ["status", "--project", str(project)]).stdout)

    assert "catalog" not in payload
