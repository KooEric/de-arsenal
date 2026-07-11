from pathlib import Path

import respx
from typer.testing import CliRunner

from pugio.cli import app

runner = CliRunner()

YAML = """
name: cli-test
state_dir: {state_dir}
source:
  type: rest
  url: https://api.test/items
  pagination: {{ mode: offset, size: 2 }}
sink:
  type: parquet
  path: {out}
"""


def write_spec(tmp_path: Path) -> Path:
    p = tmp_path / "pipe.yaml"
    p.write_text(YAML.format(state_dir=tmp_path / ".arsenal", out=tmp_path / "out"))
    return p


@respx.mock
def test_run_command(tmp_path: Path) -> None:
    respx.get("https://api.test/items").respond(json=[{"id": 1}])
    result = runner.invoke(app, ["run", str(write_spec(tmp_path))])
    assert result.exit_code == 0
    assert "written=1" in result.output


@respx.mock
def test_status_command(tmp_path: Path) -> None:
    respx.get("https://api.test/items").respond(json=[{"id": 1}])
    spec = write_spec(tmp_path)
    runner.invoke(app, ["run", str(spec)])
    result = runner.invoke(app, ["status", str(spec)])
    assert result.exit_code == 0
    assert "done" in result.output


def test_invalid_spec_exits_nonzero(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n")
    result = runner.invoke(app, ["run", str(bad)])
    assert result.exit_code == 1
    assert "source" in result.output  # 어떤 필드가 문제인지 보인다
