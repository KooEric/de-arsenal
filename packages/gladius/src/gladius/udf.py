"""Python UDF loading and DuckDB registration for Gladius."""

import importlib
from collections.abc import Callable
from typing import Any

import duckdb

from arsenal_core.errors import FatalError
from gladius.compile.ident import normalize_type
from gladius.spec import PythonUdfStep, TransformSpec


def udf_name(index: int) -> str:
    """Return the deterministic, private SQL name for a Python UDF step."""
    return f"__gladius_python_udf_{index}"


def register_python_udfs(con: duckdb.DuckDBPyConnection, spec: TransformSpec) -> None:
    """Load and register all Python UDFs declared in ``spec``."""
    for index, step in enumerate(spec.steps):
        if not isinstance(step, PythonUdfStep):
            continue
        function = _load_function(step.python)
        parameters = (
            [normalize_type(item) for item in step.arg_types]
            if step.arg_types is not None
            else None
        )
        try:
            kwargs: dict[str, Any] = {"return_type": normalize_type(step.return_type)}
            if parameters is not None:
                kwargs["parameters"] = parameters
            con.create_function(udf_name(index), function, **kwargs)
        except duckdb.Error as e:
            raise FatalError(
                f"cannot register Python UDF {step.python!r}; "
                "install gladius[python] and check the function signature: "
                f"{e}"
            ) from e


def _load_function(target: str) -> Callable[..., Any]:
    module_name, function_name = target.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
    except (ImportError, AttributeError) as e:
        raise FatalError(f"cannot load Python UDF {target!r}: {e}") from e
    if not callable(function):
        raise FatalError(f"Python UDF target is not callable: {target!r}")
    return function
