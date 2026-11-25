import pytest
from pydantic import ValidationError

from censor.grade import BLOCKER_CAP, cap_grade, score_to_grade
from censor.model import CheckResult, Dimension, Status
from censor.scoring import dimension_score, score_checks


def _check(
    status: Status,
    *,
    dimension: Dimension = Dimension.GUARDS,
    weight: int = 2,
    blocker: bool = False,
    check_id: str = "x",
) -> CheckResult:
    return CheckResult(
        id=check_id,
        dimension=dimension,
        weight=weight,
        status=status,
        blocker=blocker,
        subject="collect.yaml",
        evidence="e",
    )


@pytest.mark.parametrize(
    ("score", "grade"),
    [(100, "A+"), (95, "A+"), (94.9, "A"), (90, "A"), (80, "B"), (70, "C"), (60, "D"), (59.9, "F")],
)
def test_score_to_grade_boundaries(score: float, grade: str) -> None:
    assert score_to_grade(score) == grade


def test_cap_grade_only_lowers() -> None:
    assert cap_grade("A+", BLOCKER_CAP) == "C"
    assert cap_grade("D", BLOCKER_CAP) == "D"


def test_check_result_is_frozen_and_rejects_zero_weight() -> None:
    check = _check(Status.PASS)
    with pytest.raises(ValidationError):
        CheckResult.model_validate({**check.model_dump(), "weight": 0})
    with pytest.raises(ValidationError):
        check.status = Status.FAIL  # type: ignore[misc]


def test_warn_earns_half_and_na_error_leave_denominator() -> None:
    checks = [
        _check(Status.PASS, weight=2),
        _check(Status.WARN, weight=2),
        _check(Status.NA, weight=5),
        _check(Status.ERROR, weight=5),
    ]
    assert dimension_score(checks, Dimension.GUARDS) == pytest.approx(7.5)


def test_dimension_without_applicable_checks_is_none() -> None:
    assert dimension_score([_check(Status.NA)], Dimension.GUARDS) is None
    assert dimension_score([], Dimension.SECURITY) is None


def test_overall_score_weights_dimensions_and_skips_empty_ones() -> None:
    checks = [
        _check(Status.PASS, dimension=Dimension.CORRECTNESS),  # 10 x 20
        _check(Status.FAIL, dimension=Dimension.GUARDS),  # 0 x 15
    ]
    scored = score_checks(checks)
    assert scored.score == pytest.approx(100 * 20 / 35)
    assert scored.grade == "F"
    assert {d.dimension for d in scored.dimensions if d.score is None} >= {Dimension.FRESHNESS}


def test_failed_blocker_caps_grade_at_c() -> None:
    checks = [
        _check(Status.PASS, dimension=Dimension.CORRECTNESS, weight=50),
        _check(Status.FAIL, dimension=Dimension.SECURITY, weight=1, blocker=True),
        _check(Status.PASS, dimension=Dimension.SECURITY, weight=50),
    ]
    scored = score_checks(checks)
    assert scored.score is not None and scored.score > 95
    assert scored.grade == "C"
    assert scored.capped_by_blocker is True


def test_no_applicable_checks_gives_no_grade() -> None:
    scored = score_checks([_check(Status.NA)])
    assert scored.score is None
    assert scored.grade is None
