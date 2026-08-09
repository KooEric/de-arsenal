import pyarrow as pa
import pytest
from pydantic import ValidationError
from scutum import DataContract


def contract(**kwargs: object) -> DataContract:
    return DataContract.model_validate(
        {
            "name": "orders",
            "columns": [{"name": "id", "type": "int64", "nullable": False}],
            **kwargs,
        }
    )


def test_contract_accepts_matching_batch() -> None:
    batch = pa.RecordBatch.from_arrays(
        [pa.array([1, 2], type=pa.int64())],
        schema=pa.schema([pa.field("id", pa.int64(), nullable=False)]),
    )
    report = contract().check(batch)
    assert report.ok is True
    assert report.violations == ()


def test_contract_reports_missing_type_null_and_extra() -> None:
    report = contract().check(pa.RecordBatch.from_pylist([{"id": None, "extra": "x"}]))
    messages = {(violation.field, violation.message) for violation in report.violations}
    assert report.ok is False
    assert ("id", "contains null values") in messages
    assert ("extra", "unexpected column") in messages


def test_contract_rejects_empty_columns() -> None:
    with pytest.raises(ValidationError):
        DataContract.model_validate({"name": "empty", "columns": []})
