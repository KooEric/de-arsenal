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
