from __future__ import annotations

from typing import Any

import pytest
from ballista.plan import plan_to_mapping
from ballista.worker import apply_spark_plan

from gladius.plan import preflight_portable
from gladius.spec import TransformSpec


class Expression:
    def __init__(self, value: str) -> None:
        self.value = value

    def alias(self, name: str) -> tuple[str, str, str]:
        return ("alias", self.value, name)

    def cast(self, data_type: str) -> tuple[str, str, str]:
        return ("cast", self.value, data_type)


class Functions:
    def expr(self, expression: str) -> Expression:
        return Expression(f"expr:{expression}")

    def col(self, column: str) -> Expression:
        return Expression(f"col:{column}")

    def bround(self, column: Expression, scale: int) -> Expression:
        return Expression(f"bround({column.value},{scale})")

    def round(self, column: Expression, scale: int) -> Expression:
        return Expression(f"round({column.value},{scale})")


class DataType:
    def __init__(self, name: str) -> None:
        self.name = name

    def typeName(self) -> str:  # noqa: N802 - Spark API name
        return self.name


class Field:
    def __init__(self, name: str) -> None:
        self.dataType = DataType(name)  # noqa: N815 - Spark API name


class Frame:
    def __init__(self, types: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.types = dict(types or {"id": "long", "amount": "long"})

    @property
    def columns(self) -> list[str]:
        return list(self.types)

    @property
    def schema(self) -> dict[str, Field]:
        return {name: Field(kind) for name, kind in self.types.items()}

    def select(self, *columns: Any) -> Frame:
        self.calls.append(("select", columns))
        return self

    def filter(self, expression: Any) -> Frame:
        self.calls.append(("filter", expression.value))
        return self

    def withColumnRenamed(self, source: str, target: str) -> Frame:  # noqa: N802
        self.calls.append(("rename", (source, target)))
        self.types = {
            (target if name == source else name): kind for name, kind in self.types.items()
        }
        return self

    def withColumn(self, name: str, expression: Any) -> Frame:  # noqa: N802
        self.calls.append(("with_column", (name, expression)))
        return self

    def dropDuplicates(self, keys: list[str]) -> Frame:  # noqa: N802
        self.calls.append(("deduplicate", keys))
        return self


def test_minimal_spark_renderer_applies_only_portable_dataframe_operations() -> None:
    spec = TransformSpec.model_validate(
        {
            "name": "portable",
            "input": "s3://lake/input",
            "map": {"id": "source_id", "amount": "raw_amount"},
            "steps": [
                {"filter": "amount > 0"},
                {"rename": {"amount": "gross"}},
                {"cast": {"gross": "bigint"}},
                {"derive": {"net": "gross * 0.9"}},
                {"dedup": ["id"]},
                {"select": ["id", "net"]},
            ],
            "output": "iceberg://analytics.daily",
        }
    )
    plan = plan_to_mapping(preflight_portable(spec))
    frame = Frame()

    result = apply_spark_plan(frame, plan, Functions())

    assert result is frame
    assert [call[0] for call in frame.calls] == [
        "select",
        "filter",
        "rename",
        "select",
        "with_column",
        "with_column",
        "deduplicate",
        "select",
    ]
    assert frame.calls[4][1] == ("gross", ("cast", "col:gross", "long"))


def _single_step(step: dict[str, Any]) -> list[tuple[str, Any]]:
    spec = TransformSpec.model_validate(
        {"name": "t", "input": "s3://lake/in", "steps": [step], "output": "iceberg://a.b"}
    )
    frame = Frame({"id": "long", "a": "double", "b": "string", "d": "decimal"})
    apply_spark_plan(frame, plan_to_mapping(preflight_portable(spec)), Functions())
    return frame.calls


def test_rename_moves_renamed_columns_last_like_duckdb() -> None:
    calls = _single_step({"rename": {"a": "alpha", "id": "key"}})

    assert calls[-1] == ("select", ("b", "d", "alpha", "key"))


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("a", ("cast", "bround(col:a,0)", "int")),  # DOUBLE rounds half-to-even
        ("d", ("cast", "round(col:d,0)", "int")),  # DECIMAL rounds half-away
        ("id", ("cast", "col:id", "int")),  # integral source: plain cast
    ],
)
def test_integer_casts_round_like_duckdb(column: str, expected: tuple[str, str, str]) -> None:
    calls = _single_step({"cast": {column: "integer"}})

    assert calls == [("with_column", (column, expected))]


def test_bare_decimal_matches_gladius_precision() -> None:
    calls = _single_step({"cast": {"a": "decimal"}})

    assert calls == [("with_column", ("a", ("cast", "col:a", "decimal(38,9)")))]
