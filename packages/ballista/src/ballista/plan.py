"""Canonical LogicalPlan v1 serialization for the remote executor boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from arsenal_core.errors import FatalError
from arsenal_core.execution import canonical_hash
from gladius.plan import (
    PLAN_VERSION,
    Cast,
    ColumnCast,
    ColumnRename,
    Deduplicate,
    Derive,
    Filter,
    LogicalPlan,
    NamedExpression,
    Project,
    PythonUdf,
    RawSql,
    ReadParquet,
    Rename,
    Select,
    SqlExpression,
    WriteParquet,
    preflight_portable,
)


class PlanFormatError(FatalError):
    """A serialized plan is malformed or outside LogicalPlan v1."""


def plan_to_mapping(plan: LogicalPlan) -> dict[str, object]:
    """Serialize typed Gladius nodes without dialect SQL or arbitrary object reprs."""

    nodes: list[dict[str, object]] = []
    for node in plan.nodes:
        match node:
            case ReadParquet(source=source):
                nodes.append({"type": "read_parquet", "source": source})
            case Project(expressions=expressions):
                nodes.append(
                    {
                        "type": "project",
                        "expressions": [
                            {"name": item.name, "expression": item.expression.source}
                            for item in expressions
                        ],
                    }
                )
            case Rename(columns=columns):
                nodes.append(
                    {
                        "type": "rename",
                        "columns": [
                            {"source": item.source, "target": item.target} for item in columns
                        ],
                    }
                )
            case Cast(columns=columns):
                nodes.append(
                    {
                        "type": "cast",
                        "columns": [
                            {"column": item.column, "data_type": item.data_type} for item in columns
                        ],
                    }
                )
            case Filter(predicate=predicate):
                nodes.append({"type": "filter", "predicate": predicate.source})
            case Derive(expressions=expressions):
                nodes.append(
                    {
                        "type": "derive",
                        "expressions": [
                            {"name": item.name, "expression": item.expression.source}
                            for item in expressions
                        ],
                    }
                )
            case Deduplicate(keys=keys):
                nodes.append({"type": "deduplicate", "keys": list(keys)})
            case Select(columns=columns):
                nodes.append({"type": "select", "columns": list(columns)})
            case RawSql(expression=expression):
                nodes.append({"type": "raw_sql", "expression": expression})
            case PythonUdf(
                target=target,
                args=args,
                output=output,
                arg_types=arg_types,
                return_type=return_type,
                step_index=step_index,
            ):
                nodes.append(
                    {
                        "type": "python_udf",
                        "target": target,
                        "args": list(args),
                        "output": output,
                        "arg_types": list(arg_types) if arg_types is not None else None,
                        "return_type": return_type,
                        "step_index": step_index,
                    }
                )
            case WriteParquet(destination=destination):
                nodes.append({"type": "write_parquet", "destination": destination})
            case _:
                raise PlanFormatError(f"unsupported LogicalPlan node: {type(node).__name__}")
    return {"version": plan.version, "nodes": nodes}


def plan_from_mapping(value: Mapping[str, Any]) -> LogicalPlan:
    """Validate serialized nodes and reconstruct Gladius' frozen plan types."""

    _exact_keys(value, {"version", "nodes"}, "plan")
    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != PLAN_VERSION:
        raise PlanFormatError(f"unsupported logical plan version: {version!r}")
    raw_nodes = _sequence(value.get("nodes"), "plan.nodes")
    nodes: list[object] = []
    for index, raw_node in enumerate(raw_nodes):
        node = _mapping(raw_node, f"plan.nodes[{index}]")
        node_type = _text(node.get("type"), f"plan.nodes[{index}].type")
        nodes.append(_parse_node(node_type, node, index))
    try:
        plan = LogicalPlan(tuple(nodes), version=version)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PlanFormatError(f"invalid LogicalPlan v1: {exc}") from exc
    return plan


def require_portable_mapping(value: Mapping[str, Any]) -> LogicalPlan:
    """Reject local-only nodes and expressions before any remote side effect."""

    try:
        return preflight_portable(plan_from_mapping(value))
    except ValueError as exc:
        raise PlanFormatError(str(exc)) from exc


def plan_hash(value: LogicalPlan | Mapping[str, Any]) -> str:
    mapping = plan_to_mapping(value) if isinstance(value, LogicalPlan) else value
    return canonical_hash(mapping)


def _parse_node(node_type: str, node: Mapping[str, Any], index: int) -> object:
    location = f"plan.nodes[{index}]"
    if node_type == "read_parquet":
        _exact_keys(node, {"type", "source"}, location)
        return ReadParquet(_text(node.get("source"), f"{location}.source"))
    if node_type in {"project", "derive"}:
        _exact_keys(node, {"type", "expressions"}, location)
        expressions = tuple(
            _named_expression(item, f"{location}.expressions[{item_index}]")
            for item_index, item in enumerate(
                _sequence(node.get("expressions"), f"{location}.expressions")
            )
        )
        return Project(expressions) if node_type == "project" else Derive(expressions)
    if node_type == "rename":
        _exact_keys(node, {"type", "columns"}, location)
        columns: list[ColumnRename] = []
        for item_index, item in enumerate(_sequence(node.get("columns"), f"{location}.columns")):
            item_location = f"{location}.columns[{item_index}]"
            mapping = _mapping(item, item_location)
            _exact_keys(mapping, {"source", "target"}, item_location)
            columns.append(
                ColumnRename(
                    _text(mapping.get("source"), f"{item_location}.source"),
                    _text(mapping.get("target"), f"{item_location}.target"),
                )
            )
        return Rename(tuple(columns))
    if node_type == "cast":
        _exact_keys(node, {"type", "columns"}, location)
        casts: list[ColumnCast] = []
        for item_index, item in enumerate(_sequence(node.get("columns"), f"{location}.columns")):
            item_location = f"{location}.columns[{item_index}]"
            mapping = _mapping(item, item_location)
            _exact_keys(mapping, {"column", "data_type"}, item_location)
            casts.append(
                ColumnCast(
                    _text(mapping.get("column"), f"{item_location}.column"),
                    _text(mapping.get("data_type"), f"{item_location}.data_type"),
                )
            )
        return Cast(tuple(casts))
    if node_type == "filter":
        _exact_keys(node, {"type", "predicate"}, location)
        return Filter(SqlExpression(_text(node.get("predicate"), f"{location}.predicate")))
    if node_type == "deduplicate":
        _exact_keys(node, {"type", "keys"}, location)
        return Deduplicate(_text_tuple(node.get("keys"), f"{location}.keys"))
    if node_type == "select":
        _exact_keys(node, {"type", "columns"}, location)
        return Select(_text_tuple(node.get("columns"), f"{location}.columns"))
    if node_type == "raw_sql":
        _exact_keys(node, {"type", "expression"}, location)
        return RawSql(_text(node.get("expression"), f"{location}.expression"))
    if node_type == "python_udf":
        expected = {
            "type",
            "target",
            "args",
            "output",
            "arg_types",
            "return_type",
            "step_index",
        }
        _exact_keys(node, expected, location)
        step_index = node.get("step_index")
        if not isinstance(step_index, int) or isinstance(step_index, bool) or step_index < 0:
            raise PlanFormatError(f"{location}.step_index must be a non-negative integer")
        raw_arg_types = node.get("arg_types")
        arg_types = (
            None if raw_arg_types is None else _text_tuple(raw_arg_types, f"{location}.arg_types")
        )
        return PythonUdf(
            target=_text(node.get("target"), f"{location}.target"),
            args=_text_tuple(node.get("args"), f"{location}.args"),
            output=_text(node.get("output"), f"{location}.output"),
            arg_types=arg_types,
            return_type=_text(node.get("return_type"), f"{location}.return_type"),
            step_index=step_index,
        )
    if node_type == "write_parquet":
        _exact_keys(node, {"type", "destination"}, location)
        return WriteParquet(_text(node.get("destination"), f"{location}.destination"))
    raise PlanFormatError(f"{location}.type is not a LogicalPlan v1 node: {node_type!r}")


def _named_expression(value: Any, location: str) -> NamedExpression:
    mapping = _mapping(value, location)
    _exact_keys(mapping, {"name", "expression"}, location)
    return NamedExpression(
        _text(mapping.get("name"), f"{location}.name"),
        SqlExpression(_text(mapping.get("expression"), f"{location}.expression")),
    )


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanFormatError(f"{location} must be an object")
    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise PlanFormatError(f"{location} must be an object")
    return {cast(str, key): item for key, item in mapping.items()}


def _sequence(value: Any, location: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PlanFormatError(f"{location} must be an array")
    return tuple(cast(Sequence[object], value))


def _text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlanFormatError(f"{location} must be a non-empty string")
    return value


def _text_tuple(value: Any, location: str) -> tuple[str, ...]:
    return tuple(
        _text(item, f"{location}[{index}]") for index, item in enumerate(_sequence(value, location))
    )


def _exact_keys(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"unexpected {extra}")
        raise PlanFormatError(f"{location} has invalid fields: {', '.join(details)}")


__all__ = [
    "PlanFormatError",
    "plan_from_mapping",
    "plan_hash",
    "plan_to_mapping",
    "require_portable_mapping",
]
