"""Gladius CLI — compile/run 커맨드 (M3 Task 3.5)."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from typer.testing import CliRunner

from gladius.cli import app

runner = CliRunner()

YAML = """
name: cli-test
input: {input}
steps:
  - select: [id]
output: {output}
"""


def write_spec(tmp_path: Path, input_dir: Path, output_dir: Path) -> Path:
    p = tmp_path / "transform.yaml"
    p.write_text(YAML.format(input=input_dir, output=output_dir))
    return p


def test_compile_prints_sql_to_stdout(tmp_path: Path) -> None:
    spec = write_spec(tmp_path, tmp_path / "in", tmp_path / "out")
    result = runner.invoke(app, ["compile", str(spec)])
    assert result.exit_code == 0
    assert "WITH s0 AS (SELECT * FROM read_parquet(" in result.output
    assert "SELECT" in result.output


def test_run_produces_output(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2]}), tmp_path / "in" / "a.parquet"
    )
    spec = write_spec(tmp_path, tmp_path / "in", tmp_path / "out")
    result = runner.invoke(app, ["run", str(spec)])
    assert result.exit_code == 0
    assert (tmp_path / "out").exists()
    assert list((tmp_path / "out").glob("*.parquet"))


def test_invalid_spec_exits_1_with_field_path(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n")
    result = runner.invoke(app, ["compile", str(bad)])
    assert result.exit_code == 1
    assert "input" in result.output  # 어떤 필드가 문제인지 보인다
