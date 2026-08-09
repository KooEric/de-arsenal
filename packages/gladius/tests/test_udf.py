"""Python UDF loading and registration tests without requiring numpy."""

from types import ModuleType
from typing import Any, cast

import duckdb
import pytest

from gladius.spec import TransformSpec
from gladius.udf import register_python_udfs


class _FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict[str, Any]]] = []

    def create_function(self, name: str, function: object, **kwargs: Any) -> None:
        self.calls.append((name, function, kwargs))


def _spec(target: str = "test_udf_module:slugify") -> TransformSpec:
    return TransformSpec.model_validate(
        {
            "name": "udf",
            "input": "./in",
            "output": "./out",
            "steps": [
                {
                    "python": target,
                    "args": ["title"],
                    "arg_types": ["VARCHAR"],
                    "output": "slug",
                    "return_type": "VARCHAR",
                }
            ],
        }
    )


def test_register_python_udf_imports_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("test_udf_module")

    def slugify(value: object) -> str:
        return str(value).lower().replace(" ", "-")

    monkeypatch.setattr(module, "slugify", slugify, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "test_udf_module", module)
    connection = _FakeConnection()

    register_python_udfs(cast(duckdb.DuckDBPyConnection, connection), _spec())

    assert len(connection.calls) == 1
    name, function, kwargs = connection.calls[0]
    assert name == "__gladius_python_udf_0"
    assert callable(function)
    assert kwargs == {"parameters": ["VARCHAR"], "return_type": "VARCHAR"}


def test_register_python_udf_reports_missing_target() -> None:
    with pytest.raises(Exception, match="cannot load Python UDF"):
        register_python_udfs(
            cast(duckdb.DuckDBPyConnection, _FakeConnection()),
            _spec("missing_udf_module:slugify"),
        )
