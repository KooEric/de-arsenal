from __future__ import annotations

from typing import Any

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


class Frame:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def select(self, *columns: Any) -> Frame:
        self.calls.append(("select", columns))
        return self

    def filter(self, expression: Any) -> Frame:
        self.calls.append(("filter", expression.value))
        return self

    def withColumnRenamed(self, source: str, target: str) -> Frame:  # noqa: N802
        self.calls.append(("rename", (source, target)))
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
        "with_column",
        "with_column",
        "deduplicate",
        "select",
    ]
    assert frame.calls[3][1] == ("gross", ("cast", "col:gross", "long"))
