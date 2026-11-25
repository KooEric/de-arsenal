from datetime import UTC, datetime
from pathlib import Path

from _projects import (
    functional_project,
    minimal_project,
    production_project,
    robust_project,
)

from censor.audit import audit_project, new_run_id
from censor.model import Dimension, Status

NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)


def test_run_id_format() -> None:
    assert new_run_id("quick", NOW) == "20261125-093000-quick"


def test_grades_rise_with_maturity(tmp_path: Path) -> None:
    builders = [minimal_project, functional_project, robust_project, production_project]
    scores: list[float] = []
    for i, build in enumerate(builders):
        report = audit_project(build(tmp_path / str(i)), now=NOW)
        assert report.score is not None
        scores.append(report.score)
    assert scores == sorted(scores) and len(set(scores)) == 4
    assert scores[0] < 80
    production = audit_project(tmp_path / "3", now=NOW)
    assert production.grade in ("A+", "A")


def test_quick_audit_has_no_standard_dimension_scores(tmp_path: Path) -> None:
    report = audit_project(production_project(tmp_path), now=NOW)
    by_dim = {d.dimension: d.score for d in report.dimensions}
    assert by_dim[Dimension.FRESHNESS] is None
    assert by_dim[Dimension.CORRECTNESS] == 10.0
    assert report.tier == "quick"
    assert report.run_id == "20261125-093000-quick"


def test_literal_secret_caps_production_project_at_c(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    collect = root / "collect.yaml"
    # a valid field, so correctness still passes and only the security blocker lowers the grade
    collect.write_text(
        collect.read_text().replace("path: ./input/*.csv", "path: s3://u:pw@h/input/*.csv")
    )
    report = audit_project(root, now=NOW)
    assert report.score is not None and report.score >= 80  # B before the cap
    assert report.grade == "C"
    assert report.capped_by_blocker is True
    assert report.has_failures
    assert any(c.status == Status.FAIL and c.blocker for c in report.checks)
