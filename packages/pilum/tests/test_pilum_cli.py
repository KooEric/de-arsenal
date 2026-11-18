"""Spec validation and the CLI surface."""

from __future__ import annotations

import json
import re
from importlib import metadata
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml
from typer.testing import CliRunner

from arsenal_core.errors import FatalError
from pilum.cli import app
from pilum.loader import load_dispatch
from pilum.spec import DispatchSpec

runner = CliRunner()


def base(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "name": "d",
        "source": {"type": "parquet", "path": "data/x"},
        "key": ["id"],
        "destination": {"type": "http", "url": "https://api.test/x"},
    }
    raw.update(overrides)
    return raw


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"mode": "mirror"}, "requires `destination.delete`"),
        ({"key": ["id", "id"]}, "distinct"),
        ({"key": []}, "at least 1"),
        ({"name": "../escape"}, "pattern"),
        ({"source": {"type": "parquet", "sql": "SELECT 1"}}, "takes `path`"),
        ({"source": {"type": "query", "path": "x"}}, "takes `sql`"),
        ({"destination": {"type": "slack", "url": "https://h"}}, "requires `text`"),
        (
            {"destination": {"type": "slack", "url": "https://h", "text": "t", "body": {}}},
            "takes `text` only",
        ),
        ({"destination": {"type": "http", "url": "https://h", "text": "t"}}, "for slack"),
        (
            {
                "mode": "mirror",
                "destination": {
                    "type": "http",
                    "url": "https://h",
                    "delete": {"url": "https://h/{id}/{email}"},
                },
            },
            "only use key columns",
        ),
        ({"unknown_field": 1}, "Extra inputs"),
    ],
)
def test_invalid_specs_are_rejected(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DispatchSpec.model_validate(base(**overrides))


def test_slack_cannot_mirror() -> None:
    destination = {"type": "slack", "url": "https://h", "text": "t"}
    with pytest.raises(ValueError, match="slack cannot delete"):
        DispatchSpec.model_validate(base(mode="mirror", destination=destination))


def test_loader_reports_field_paths_and_keeps_env_placeholders(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(base(mode="sideways")), encoding="utf-8")
    with pytest.raises(FatalError, match="mode: "):
        load_dispatch(bad)
    with pytest.raises(FatalError, match="not found"):
        load_dispatch(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("name: [unclosed", encoding="utf-8")
    with pytest.raises(FatalError, match="invalid YAML"):
        load_dispatch(broken)

    good = tmp_path / "good.yaml"
    raw = base()
    raw["destination"]["headers"] = {"Authorization": "Bearer ${NEVER_SET_ANYWHERE}"}
    good.write_text(yaml.safe_dump(raw), encoding="utf-8")
    spec = load_dispatch(good)
    assert spec.destination.headers["Authorization"] == "Bearer ${NEVER_SET_ANYWHERE}"


def _project(tmp_path: Path, rows: int = 3) -> Path:
    data = tmp_path / "data" / "x"
    data.mkdir(parents=True)
    table = pa.Table.from_pylist([{"id": i, "v": i * 2} for i in range(rows)])
    pq.write_table(table, data / "part-0.parquet")  # pyright: ignore[reportUnknownMemberType]
    raw = base(
        source={"type": "parquet", "path": str(data)},
        state_dir=str(tmp_path / ".arsenal"),
        # Port 9 (discard) on localhost: refused immediately, no network needed.
        destination={"type": "http", "url": "http://127.0.0.1:9/x"},
        retry={"max_attempts": 1, "base_wait_seconds": 0},
        safety={"max_consecutive_failures": 2},
    )
    spec_path = tmp_path / "d.yaml"
    spec_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return spec_path


def test_cli_plan_status_replay_reset(tmp_path: Path) -> None:
    spec_path = _project(tmp_path)

    plan = runner.invoke(app, ["plan", str(spec_path)])
    assert plan.exit_code == 0, plan.output
    payload = json.loads(plan.stdout)
    assert (payload["insert"], payload["first_dispatch"]) == (3, True)
    assert payload["sample_keys"]["insert"][0] == {"id": 0}
    assert not (tmp_path / ".arsenal").exists()

    status = runner.invoke(app, ["status", str(spec_path)])
    assert status.exit_code == 0 and json.loads(status.stdout)["ledger_size"] == 0

    replay = runner.invoke(app, ["replay", str(spec_path)])
    assert replay.exit_code == 0 and json.loads(replay.stdout)["requeued"] == 0

    dry = runner.invoke(app, ["reset", str(spec_path)])
    assert dry.exit_code == 0 and json.loads(dry.stdout)["action"] == "dry-run"
    applied = runner.invoke(app, ["reset", str(spec_path), "--confirm"])
    assert json.loads(applied.stdout)["action"] == "reset"


def test_cli_run_against_a_dead_endpoint_exits_1_and_status_alerts(tmp_path: Path) -> None:
    spec_path = _project(tmp_path)

    result = runner.invoke(app, ["run", str(spec_path)])

    assert result.exit_code == 1
    report = json.loads(result.stdout.splitlines()[0])
    assert report["ok"] is False and "unavailable" in report["aborted"]
    status = runner.invoke(app, ["status", str(spec_path)])
    assert status.exit_code == 1 and json.loads(status.stdout)["alert"] is True


def test_cli_errors_are_one_line(tmp_path: Path) -> None:
    result = runner.invoke(app, ["plan", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1 and "error: dispatch spec file not found" in result.output

    spec_path = _project(tmp_path, rows=5)
    raw = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    raw["safety"]["max_first_dispatch"] = 1
    spec_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    refused = runner.invoke(app, ["run", str(spec_path)])
    assert refused.exit_code == 1 and "--confirm-initial-load" in refused.output


def test_de_pilum_installs_without_the_other_tools() -> None:
    """PL-0: `pip install de-pilum` needs arsenal-core only; nothing depends on it."""

    def required(dist: str) -> set[str]:
        names: set[str] = set()
        for requirement in metadata.requires(dist) or []:
            if "extra" in requirement.partition(";")[2]:
                continue
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
            if match:
                names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
        return names

    others = {"pugio", "de-gladius", "de-hasta", "de-ballista", "de-aquila", "de-arsenal"}
    assert "arsenal-core" in required("de-pilum")
    assert required("de-pilum") & (others | {"de-scutum", "de-scorpio", "spatha"}) == set()
    for dist in others:
        assert "de-pilum" not in required(dist), dist
