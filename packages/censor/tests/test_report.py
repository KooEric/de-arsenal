from datetime import UTC, datetime
from pathlib import Path

from _projects import minimal_project

from censor.audit import audit_project
from censor.report import render_markdown

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def test_markdown_shows_grade_dimensions_and_fixes(tmp_path: Path) -> None:
    report = audit_project(minimal_project(tmp_path), now=NOW)
    text = render_markdown(report)
    assert text.startswith(f"# Censor report — {report.run_id}")
    assert f"**Grade {report.grade}**" in text
    assert "| correctness |" in text
    assert "guards.validate:collect.yaml" in text
    assert "trust skill" in text  # the fix line is shown
    assert "| freshness | n/a |" in text


def test_korean_labels(tmp_path: Path) -> None:
    report = audit_project(minimal_project(tmp_path), now=NOW)
    text = render_markdown(report, "ko")
    assert "등급" in text and "고칠 항목" in text
