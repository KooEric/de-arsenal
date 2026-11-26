import builtins
import json
import re
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


def _fail_import(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "censor.cli":
            raise ModuleNotFoundError(f"No module named {missing!r}", name=missing)
        return real_import(name, *args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(builtins, "__import__", fake_import)


@pytest.mark.parametrize("missing", ["censor", "censor.cli"])
def test_arsenal_eval_without_censor_prints_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _fail_import(monkeypatch, missing)
    result = runner.invoke(app, ["eval", str(tmp_path)])
    assert result.exit_code == 2
    assert "de-censor" in result.stderr


def test_arsenal_eval_with_a_broken_censor_install_does_not_hide_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_import(monkeypatch, "pyarrow")
    result = runner.invoke(app, ["eval", str(tmp_path)])
    assert isinstance(result.exception, ModuleNotFoundError)
    assert result.exception.name == "pyarrow"
    assert "de-censor" not in result.stderr


def test_arsenal_eval_passes_tier_through(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    quick = runner.invoke(app, ["eval", str(root), "--tier", "quick", "--json-only"])
    assert quick.exit_code == 1
    assert json.loads(quick.stdout)["tier"] == "quick"
    standard = runner.invoke(app, ["eval", str(root), "--tier", "standard", "--json-only"])
    assert standard.exit_code == 1
    assert json.loads(standard.stdout)["tier"] == "standard"


def test_arsenal_eval_help_shows_the_options_it_forwards() -> None:
    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)  # CI terminals get styled help
    assert "--tier" in plain and "--files" in plain


def test_arsenal_eval_forwards_a_data_target(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("id,v\n1,a\n1,b\n")
    args = [str(tmp_path), "--files", str(tmp_path / "*.csv"), "--key", "id", "--json-only"]
    via_arsenal = runner.invoke(app, ["eval", *args])
    via_censor = runner.invoke(censor_app, ["audit", *args])
    assert via_arsenal.exit_code == via_censor.exit_code == 1
    assert _without_run_id(via_arsenal.stdout) == _without_run_id(via_censor.stdout)
