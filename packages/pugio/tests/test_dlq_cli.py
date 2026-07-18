from pathlib import Path

import respx
from typer.testing import CliRunner

from pugio.cli import app

runner = CliRunner()

VALIDATE_YAML = """
name: dlq-cli-test
state_dir: {state_dir}
source:
  type: file
  path: {glob}
sink:
  type: parquet
  path: {out}
validate:
  rules:
    - field: v
      not_null: true
  on_violation: quarantine
"""


def write_validate_spec(tmp_path: Path) -> Path:
    (tmp_path / "a.csv").write_text("id,v\n1,x\n")
    (tmp_path / "b.csv").write_text("id,v\n2,\n")  # not_null 위반 → quarantine
    p = tmp_path / "pipe.yaml"
    p.write_text(
        VALIDATE_YAML.format(
            state_dir=tmp_path / ".arsenal",
            glob=str(tmp_path / "*.csv"),
            out=tmp_path / "out",
        )
    )
    return p


def test_dlq_list_shows_quarantined(tmp_path: Path) -> None:
    spec = write_validate_spec(tmp_path)
    result = runner.invoke(app, ["run", str(spec)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["dlq", "list", str(spec)])
    assert result.exit_code == 0
    assert "b.csv" in result.output


def test_dlq_list_no_quarantine(tmp_path: Path) -> None:
    (tmp_path / "clean.csv").write_text("id,v\n1,x\n")
    p = tmp_path / "clean.yaml"
    p.write_text(
        """
name: clean-t
state_dir: {state_dir}
source:
  type: file
  path: {glob}
sink:
  type: parquet
  path: {out}
""".format(
            state_dir=tmp_path / ".arsenal",
            glob=str(tmp_path / "clean.csv"),
            out=tmp_path / "out",
        )
    )
    result = runner.invoke(app, ["run", str(p)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["dlq", "list", str(p)])
    assert result.exit_code == 0
    assert "no quarantined units" in result.output


def test_dlq_retry_requeues_and_removes_files(tmp_path: Path) -> None:
    from arsenal_core.spec import load_pipeline
    from arsenal_core.state import StateStore, UnitSpec

    spec_path = write_validate_spec(tmp_path)
    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code == 0

    spec = load_pipeline(spec_path)
    assert spec.source.type == "file"
    unit_id = UnitSpec.create(
        pipeline=spec.name, source=spec.source.path, unit_key="b.csv", payload={}
    ).unit_id

    result = runner.invoke(app, ["dlq", "retry", str(spec_path), "--unit", unit_id])
    assert result.exit_code == 0

    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        rec = store.get(unit_id)
    finally:
        store.close()
    assert rec.status == "pending"

    dlq_dir = tmp_path / ".arsenal" / "dlq" / spec.name
    assert not (dlq_dir / f"{unit_id}.parquet").exists()
    assert not (dlq_dir / f"{unit_id}.json").exists()


@respx.mock
def test_dlq_retry_invalid_spec_exits_nonzero(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\n")
    result = runner.invoke(app, ["dlq", "retry", str(bad), "--unit", "whatever"])
    assert result.exit_code == 1
    assert "error" in result.output
