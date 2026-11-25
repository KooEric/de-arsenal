import json
import sys
from pathlib import Path

import pytest
from arsenal.cli import app
from typer.testing import CliRunner

from censor.cli import app as censor_app

runner = CliRunner()

MANIFEST = "name: demo\npipelines:\n  - collect.yaml\n"
COLLECT = (
    "name: demo\nsource:\n  type: file\n  path: ./in/*.csv\n  format: csv\n"
    "sink:\n  type: parquet\n  path: ./out\n"
)


def _project(root: Path) -> Path:
    root.mkdir()
    (root / "arsenal.yaml").write_text(MANIFEST)
    (root / "collect.yaml").write_text(COLLECT)
    return root


def _without_run_id(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    payload.pop("run_id")
    return payload


def test_arsenal_eval_matches_censor_audit(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    via_arsenal = runner.invoke(app, ["eval", str(root), "--json-only"])
    via_censor = runner.invoke(censor_app, ["audit", str(root), "--json-only"])
    assert via_arsenal.exit_code == via_censor.exit_code == 1
    assert _without_run_id(via_arsenal.stdout) == _without_run_id(via_censor.stdout)


def test_arsenal_eval_without_censor_prints_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "censor.cli", None)
    result = runner.invoke(app, ["eval", str(tmp_path)])
    assert result.exit_code == 2
    assert "de-censor" in result.stderr
