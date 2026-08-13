"""Arsenal 우산 CLI 테스트 (M4 Task 4.0) — init/run/query, 위임만 하고 로직은 없다."""

import json
import re
from pathlib import Path

import pyarrow as pa
import pytest
from arsenal.cli import app
from arsenal.project import load_project
from typer.testing import CliRunner

from arsenal_core.errors import FatalError

runner = CliRunner()

MANIFEST = """\
name: my-project
pipelines:
  - collect.yaml
transforms:
  - transform.yaml
"""

DBT_MANIFEST = """\
name: my-project
pipelines: []
transforms:
  - dbt: ./dbt_project
"""


def test_manifest_parses_and_paths_resolve_relative_to_file(tmp_path: Path) -> None:
    (tmp_path / "arsenal.yaml").write_text(MANIFEST, encoding="utf-8")
    proj = load_project(tmp_path / "arsenal.yaml")
    assert proj.name == "my-project"
    assert proj.pipelines[0] == tmp_path / "collect.yaml"
    assert proj.transforms[0] == tmp_path / "transform.yaml"


def test_manifest_resolves_dbt_project_reference(tmp_path: Path) -> None:
    (tmp_path / "arsenal.yaml").write_text(DBT_MANIFEST, encoding="utf-8")
    proj = load_project(tmp_path / "arsenal.yaml")
    assert len(proj.transforms) == 1
    assert proj.transforms[0].dbt == tmp_path / "dbt_project"  # type: ignore[union-attr]


def test_load_project_missing_manifest_raises_fatal_error(tmp_path: Path) -> None:
    with pytest.raises(FatalError):
        load_project(tmp_path / "arsenal.yaml")


def test_load_project_bad_yaml_raises_fatal_error(tmp_path: Path) -> None:
    (tmp_path / "arsenal.yaml").write_text("name: [unterminated", encoding="utf-8")
    with pytest.raises(FatalError):
        load_project(tmp_path / "arsenal.yaml")


def test_load_project_missing_required_field_raises_fatal_error(tmp_path: Path) -> None:
    (tmp_path / "arsenal.yaml").write_text("pipelines: []\n", encoding="utf-8")
    with pytest.raises(FatalError, match="name"):
        load_project(tmp_path / "arsenal.yaml")


def test_run_executes_pipelines_then_transforms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "arsenal.yaml").write_text(MANIFEST, encoding="utf-8")
    calls: list[str] = []

    def fake_run_pipeline(p: Path) -> None:
        calls.append(f"collect:{p.name}")

    def fake_run_transform(p: Path) -> None:
        calls.append(f"transform:{p.name}")

    monkeypatch.setattr("arsenal.cli._run_pipeline", fake_run_pipeline)
    monkeypatch.setattr("arsenal.cli._run_transform", fake_run_transform)
    result = runner.invoke(app, ["run", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls == ["collect:collect.yaml", "transform:transform.yaml"]


def test_run_executes_dbt_reference_after_pipelines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "arsenal.yaml").write_text(DBT_MANIFEST, encoding="utf-8")
    (tmp_path / "dbt_project").mkdir()
    calls: list[str] = []

    def fake_run_dbt(path: Path) -> None:
        calls.append(path.name)

    monkeypatch.setattr("arsenal.cli._run_dbt", fake_run_dbt)
    result = runner.invoke(app, ["run", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls == ["dbt_project"]


def test_run_bad_manifest_exits_with_clean_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "--project", str(tmp_path)])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "Traceback" not in result.output


def test_run_missing_pipeline_spec_exits_clean_and_names_file(tmp_path: Path) -> None:
    """valid arsenal.yaml → collect.yaml referenced but absent — no raw traceback."""
    (tmp_path / "arsenal.yaml").write_text(MANIFEST, encoding="utf-8")
    # collect.yaml intentionally not created
    result = runner.invoke(app, ["run", "--project", str(tmp_path)])
    assert result.exit_code == 1
    assert result.output.startswith("error:")
    assert "Traceback" not in result.output
    assert "collect.yaml" in result.output


def test_run_missing_transform_spec_exits_clean_and_names_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """valid arsenal.yaml → transform.yaml referenced but absent — no raw traceback."""
    (tmp_path / "arsenal.yaml").write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "collect.yaml").write_text(
        "unused", encoding="utf-8"
    )  # pipeline step is stubbed below

    def fake_run_pipeline(p: Path) -> None:
        pass

    monkeypatch.setattr("arsenal.cli._run_pipeline", fake_run_pipeline)
    # transform.yaml intentionally not created
    result = runner.invoke(app, ["run", "--project", str(tmp_path)])
    assert result.exit_code == 1
    assert result.output.startswith("error:")
    assert "Traceback" not in result.output
    assert "transform.yaml" in result.output


def test_init_happy_path_copies_recipe_and_prints_guidance(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    result = runner.invoke(app, ["init", "csv-cleanup", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert (dest / "arsenal.yaml").exists()
    assert (dest / "collect.yaml").exists()
    assert (dest / "transform.yaml").exists()
    assert (dest / "README.md").exists()
    assert "arsenal run" in result.output


def test_init_refuses_to_overwrite_existing_files(tmp_path: Path) -> None:
    dest = tmp_path / "proj"
    dest.mkdir()
    (dest / "arsenal.yaml").write_text("keep me", encoding="utf-8")
    result = runner.invoke(app, ["init", "csv-cleanup", "--dest", str(dest)])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert (dest / "arsenal.yaml").read_text(encoding="utf-8") == "keep me"
    assert not (dest / "collect.yaml").exists()  # 부분 복사 없음 — 전부 아니면 전무


def test_init_list_prints_available_recipes() -> None:
    result = runner.invoke(app, ["init", "--list"])
    assert result.exit_code == 0
    assert "github-issues" in result.output
    assert "csv-cleanup" in result.output


def test_init_unknown_recipe_lists_available(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "does-not-exist", "--dest", str(tmp_path / "x")])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "github-issues" in result.output


def test_init_without_recipe_or_list_is_a_clean_error() -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "error:" in result.output


def test_query_delegates_to_gladius_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    table = pa.table({"a": [1, 2]})

    def fake_query(sql: str) -> pa.Table:
        return table

    monkeypatch.setattr("gladius.engine.query", fake_query)
    result = runner.invoke(app, ["query", "SELECT 1", "--format", "jsonl"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.strip().splitlines()]
    assert rows == [{"a": 1}, {"a": 2}]


def test_query_wraps_arsenal_error_as_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(sql: str) -> pa.Table:
        raise FatalError("bad sql")

    monkeypatch.setattr("gladius.engine.query", boom)
    result = runner.invoke(app, ["query", "not sql"])
    assert result.exit_code == 1
    assert "error: bad sql" in result.output


def test_query_format_table_renders_via_duckdb(monkeypatch: pytest.MonkeyPatch) -> None:
    table = pa.table({"a": [1, 2]})

    def fake_query(sql: str) -> pa.Table:
        return table

    monkeypatch.setattr("gladius.engine.query", fake_query)
    result = runner.invoke(app, ["query", "SELECT 1", "--format", "table"])
    assert result.exit_code == 0, result.output
    assert "a" in result.output
    assert "1" in result.output
    assert "2" in result.output


def test_query_format_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    table = pa.table({"a": [1, 2]})

    def fake_query(sql: str) -> pa.Table:
        return table

    monkeypatch.setattr("gladius.engine.query", fake_query)
    result = runner.invoke(app, ["query", "SELECT 1", "--format", "csv"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0].strip('"') == "a"
    assert lines[1] == "1"
    assert lines[2] == "2"


def test_query_format_unsupported_is_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    table = pa.table({"a": [1, 2]})

    def fake_query(sql: str) -> pa.Table:
        return table

    monkeypatch.setattr("gladius.engine.query", fake_query)
    result = runner.invoke(app, ["query", "SELECT 1", "--format", "bogus"])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "bogus" in result.output


def test_load_project_bad_yaml_error_is_one_line_with_position(tmp_path: Path) -> None:
    """매니페스트도 스펙 로더와 같은 한 줄 요약 형식 — pyyaml 여러 줄 덤프 금지."""
    (tmp_path / "arsenal.yaml").write_text("name: x\npipelines: [1,2\n", encoding="utf-8")
    with pytest.raises(FatalError) as exc_info:
        load_project(tmp_path / "arsenal.yaml")
    message = str(exc_info.value)
    assert "invalid YAML" in message
    assert "while parsing a flow sequence" in message
    assert re.search(r"\(line \d+, column \d+\)", message)
    assert "^" not in message
    assert message.count("\n") == 0


def test_load_project_non_utf8_manifest_raises_fatal_error(tmp_path: Path) -> None:
    (tmp_path / "arsenal.yaml").write_bytes(b"name: \xff\xfe not utf-8\n")
    with pytest.raises(FatalError, match="UTF-8"):
        load_project(tmp_path / "arsenal.yaml")
