"""E2E: examples/pilum dispatches to the bundled fake endpoint over real HTTP (offline).

Runs the real ``fake_alerts_api`` on an ephemeral local port and the real CLI
with the example spec, so the README walkthrough cannot drift.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import threading
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pilum.cli import app

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "pilum"
runner = CliRunner()


def _load_api() -> Any:
    spec = importlib.util.spec_from_file_location("fake_alerts_api", EXAMPLE / "fake_alerts_api.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def alerts_url() -> Iterator[str]:
    api = _load_api()
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.AlertsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/alerts"
    finally:
        server.shutdown()
        server.server_close()


def _endpoint(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read())


def test_example_dispatch_sends_each_change_once(
    alerts_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A private copy of the example, laid out like the repository root.
    shutil.copytree(REPO / "examples" / "pilum", tmp_path / "examples" / "pilum")
    shutil.copytree(REPO / "examples" / "quickstart", tmp_path / "examples" / "quickstart")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALERTS_URL", alerts_url)
    spec = "examples/pilum/alerts.yaml"

    plan = runner.invoke(app, ["plan", spec])
    assert plan.exit_code == 0, plan.output
    assert json.loads(plan.stdout)["insert"] == 2

    first = runner.invoke(app, ["run", spec])
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["inserted"] == 2
    held = _endpoint(alerts_url)
    assert held["alerts"]["2"] == {"customer": "Bob", "amount": 17.0, "note": "order 2 is pending"}
    assert sorted(held["alerts"]) == ["2", "4"]

    again = runner.invoke(app, ["run", spec])
    assert json.loads(again.stdout)["unchanged"] == 2
    assert _endpoint(alerts_url)["applied"] == 2

    # Bob pays: his alert is deleted. Dave's refund amount changes: one update.
    orders = tmp_path / "examples" / "quickstart" / "orders.csv"
    text = orders.read_text(encoding="utf-8")
    text = text.replace("2,Bob,17.00,pending", "2,Bob,17.00,paid")
    orders.write_text(
        text.replace("4,Dave,5.25,refunded", "4,Dave,6.00,refunded"), encoding="utf-8"
    )

    changed = runner.invoke(app, ["run", spec])
    assert changed.exit_code == 0, changed.output
    report = json.loads(changed.stdout)
    assert (report["updated"], report["deleted"], report["inserted"]) == (1, 1, 0)
    held = _endpoint(alerts_url)
    assert sorted(held["alerts"]) == ["4"] and held["alerts"]["4"]["amount"] == 6.0
    assert held["duplicates"] == 0

    status = runner.invoke(app, ["status", spec])
    assert status.exit_code == 0 and json.loads(status.stdout)["ledger_size"] == 1
