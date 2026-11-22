"""Exercise publication, discovery and the operator's graph through the actual CLI commands."""

import json
from pathlib import Path
from typing import Any

import pytest
from arsenal.cli import app as arsenal
from typer.testing import CliRunner

from aquila.cli import app as aquila


def test_revision_and_failed_attempt_share_one_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    folder = Path("input")
    folder.mkdir()
    source = folder / "sensor.csv"
    args = ["dataset", "apply"]
    source.write_text("id,day\na,2026-01-01\n")
    first = runner.invoke(arsenal, args + ["--files", "input/*.csv", "--time-column", "day"])
    assert first.exit_code == 0, first.output

    def graph() -> dict[str, Any]:
        scan = runner.invoke(aquila, ["scan"])
        assert scan.exit_code == 0 and not json.loads(scan.output)["skipped"], scan.output
        tree = runner.invoke(aquila, ["lineage", "published", "--direction", "up"])
        assert tree.exit_code == 0, tree.output
        return json.loads(tree.output)

    initial = graph()["publications"]["published"]
    assert initial["rows"] == 1
    source.write_text("id,day\na,2026-01-02\nb,2026-01-03\n")
    second = runner.invoke(arsenal, args)
    assert second.exit_code == 0, second.output
    revised = graph()["publications"]["published"]
    assert revised["version"] != initial["version"]
    assert revised["latest_attempt"]["status"] == "succeeded"
    assert revised["published_at"] == json.loads(second.output)["last_success"]
    assert revised["files"] == ["input/sensor.csv"]
    assert revised["rows"] == 2
    assert revised["period_start"].startswith("2026-01-02")
    assert revised["period_end"].startswith("2026-01-03")

    source.write_text("id,day,extra\na,2026-02-01,bad\n")
    third = runner.invoke(arsenal, args)
    assert third.exit_code == 1, third.output
    failed_graph = graph()
    assert failed_graph["nodes"] == ["input", "published"]
    assert len(failed_graph["edges"]) == 1
    failed = failed_graph["publications"]["published"]
    for field in ("version", "published_at", "rows", "period_start", "period_end", "files"):
        assert failed[field] == revised[field]
    assert failed["latest_attempt"]["status"] == "failed"
    assert "자료형" in failed["latest_attempt"]["error"]
    chart = runner.invoke(aquila, ["lineage", "published", "--format", "mermaid"])
    assert chart.exit_code == 0
    for label in (
        "input",
        "dataset apply",
        "Latest apply: failed",
        failed["latest_attempt"]["at"],
        revised["published_at"],
        "2 rows",
        revised["period_start"],
        revised["period_end"],
    ):
        assert label in chart.output
    actual = runner.invoke(
        arsenal,
        [
            "query",
            "ATTACH '.arsenal/dataset.duckdb' AS ds (READ_ONLY); "
            "SELECT day FROM ds.published ORDER BY day",
            "--format",
            "jsonl",
        ],
    )
    assert [json.loads(r)["day"] for r in actual.output.splitlines()] == [
        "2026-01-02",
        "2026-01-03",
    ]
