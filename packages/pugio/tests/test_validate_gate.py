import pyarrow as pa
import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import ValidateRule
from pugio.validate.gate import check


def test_not_null_violation_detected() -> None:
    batch = pa.RecordBatch.from_pylist([{"v": 1}, {"v": None}, {"v": 3}])
    report = check(batch, [ValidateRule(field="v", not_null=True)])
    assert report.ok is False
    assert [(v.rule, v.field, v.count) for v in report.violations] == [("not_null", "v", 1)]


def test_range_and_unique() -> None:
    batch = pa.RecordBatch.from_pylist([{"v": 1}, {"v": 1}, {"v": 99}])
    report = check(batch, [ValidateRule(field="v", unique=True, max=10)])
    assert report.ok is False
    rules_hit = {v.rule for v in report.violations}
    assert rules_hit == {"unique", "max"}


def test_clean_batch_passes() -> None:
    batch = pa.RecordBatch.from_pylist([{"v": 1}, {"v": 2}, {"v": 3}])
    report = check(batch, [ValidateRule(field="v", not_null=True, unique=True, min=0, max=10)])
    assert report.ok is True
    assert report.violations == []


def test_missing_field_is_fatal() -> None:
    batch = pa.RecordBatch.from_pylist([{"v": 1}])
    with pytest.raises(FatalError, match="not in batch schema"):
        check(batch, [ValidateRule(field="nope", not_null=True)])


def test_min_on_non_numeric_field_is_fatal() -> None:
    """min/max on a string column raises a raw pyarrow ArrowNotImplementedError —
    gate.check() must translate it into a FatalError with a clear message (M2-E FIX 4)."""
    batch = pa.RecordBatch.from_pylist([{"v": "a"}, {"v": "b"}])
    with pytest.raises(FatalError, match="min/max requires a numeric field"):
        check(batch, [ValidateRule(field="v", min=0)])


def test_max_on_non_numeric_field_is_fatal() -> None:
    batch = pa.RecordBatch.from_pylist([{"v": "a"}, {"v": "b"}])
    with pytest.raises(FatalError, match="min/max requires a numeric field"):
        check(batch, [ValidateRule(field="v", max=10)])


def test_unique_ignores_nulls_like_min_max() -> None:
    """null은 unique 위반이 아니다 — min/max와 같은 null 규율을 따른다.

    pc.count_distinct의 기본 mode="only_valid"는 null을 세지 않는데 len(col)에서
    그대로 빼면, null 2개가 있는 컬럼이 중복 2건으로 보고된다(오탐). null을 막고
    싶으면 not_null 규칙을 따로 붙이는 게 이 코드베이스의 규약이다.
    """
    batch = pa.RecordBatch.from_pylist([{"v": 1}, {"v": 2}, {"v": None}, {"v": None}])
    report = check(batch, [ValidateRule(field="v", unique=True)])
    assert report.violations == []


def test_unique_still_detects_real_duplicates_with_nulls_present() -> None:
    batch = pa.RecordBatch.from_pylist([{"v": 1}, {"v": 1}, {"v": None}])
    report = check(batch, [ValidateRule(field="v", unique=True)])
    assert [(v.rule, v.field, v.count) for v in report.violations] == [("unique", "v", 1)]
