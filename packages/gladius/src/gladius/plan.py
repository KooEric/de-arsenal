"""Engine-neutral transform plan and portability preflight.

``TransformSpec`` deliberately remains the user-facing format.  This module is
the boundary between that format and an executor: it records the transform as
typed, immutable nodes and reports whether those nodes can be sent to another
SQL engine.

LogicalPlan v1 has a deliberately small portable surface:

* map/project, rename, select, cast, and deduplicate;
* ANSI-compatible filter and derive expressions; and
* Parquet reads and writes.

Raw ``sql:`` steps and Python UDFs remain valid for the local DuckDB executor,
but are intentionally marked engine-local.  A future executor should call
``preflight_portable`` before submitting work rather than attempting to reuse
DuckDB SQL directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias, cast

from gladius.spec import (
    CastStep,
    DedupStep,
    DeriveStep,
    FilterStep,
    PythonUdfStep,
    RenameStep,
    SelectStep,
    SqlStep,
    Step,
    TransformSpec,
)

PLAN_VERSION = 1


@dataclass(frozen=True)
class SqlExpression:
    """An expression from the transform spec, preserved without a dialect renderer.

    v1 keeps the source form so the local renderer retains byte-for-byte SQL
    compatibility.  ``portability_report`` validates the restricted ANSI
    expression subset before a remote executor is allowed to use the plan.
    """

    source: str


@dataclass(frozen=True)
class NamedExpression:
    name: str
    expression: SqlExpression


@dataclass(frozen=True)
class ColumnRename:
    source: str
    target: str


@dataclass(frozen=True)
class ColumnCast:
    column: str
    data_type: str


@dataclass(frozen=True)
class ReadParquet:
    source: str


@dataclass(frozen=True)
class Project:
    expressions: tuple[NamedExpression, ...]


@dataclass(frozen=True)
class Rename:
    columns: tuple[ColumnRename, ...]


@dataclass(frozen=True)
class Cast:
    columns: tuple[ColumnCast, ...]


@dataclass(frozen=True)
class Filter:
    predicate: SqlExpression


@dataclass(frozen=True)
class Derive:
    expressions: tuple[NamedExpression, ...]


@dataclass(frozen=True)
class Deduplicate:
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Select:
    columns: tuple[str, ...]


@dataclass(frozen=True)
class RawSql:
    """DuckDB-local SQL escape hatch; not portable by design."""

    expression: str


@dataclass(frozen=True)
class PythonUdf:
    """DuckDB-local registered Python function; not portable by design."""

    target: str
    args: tuple[str, ...]
    output: str
    arg_types: tuple[str, ...] | None
    return_type: str
    step_index: int


@dataclass(frozen=True)
class WriteParquet:
    destination: str


PlanNode: TypeAlias = (
    ReadParquet
    | Project
    | Rename
    | Cast
    | Filter
    | Derive
    | Deduplicate
    | Select
    | RawSql
    | PythonUdf
    | WriteParquet
)

Operation: TypeAlias = (
    Project | Rename | Cast | Filter | Derive | Deduplicate | Select | RawSql | PythonUdf
)


@dataclass(frozen=True)
class LogicalPlan:
    """Versioned, engine-neutral representation of a Gladius transform."""

    nodes: tuple[PlanNode, ...]
    version: int = PLAN_VERSION

    def __post_init__(self) -> None:
        if self.version != PLAN_VERSION:
            raise ValueError(f"unsupported logical plan version: {self.version}")
        if len(self.nodes) < 2:
            raise ValueError("logical plan requires a read and a write node")
        if not isinstance(self.nodes[0], ReadParquet):
            raise ValueError("logical plan must begin with ReadParquet")
        if not isinstance(self.nodes[-1], WriteParquet):
            raise ValueError("logical plan must end with WriteParquet")
        if any(isinstance(node, (ReadParquet, WriteParquet)) for node in self.nodes[1:-1]):
            raise ValueError("read and write nodes may only appear at the plan boundaries")

    @property
    def read(self) -> ReadParquet:
        return cast(ReadParquet, self.nodes[0])

    @property
    def operations(self) -> tuple[Operation, ...]:
        return tuple(cast(Operation, node) for node in self.nodes[1:-1])

    @property
    def write(self) -> WriteParquet:
        return cast(WriteParquet, self.nodes[-1])


def build_logical_plan(spec: TransformSpec) -> LogicalPlan:
    """Translate a validated spec into LogicalPlan v1 without changing its semantics."""

    nodes: list[PlanNode] = [ReadParquet(spec.input)]
    if spec.map:
        nodes.append(
            Project(
                tuple(
                    NamedExpression(name, SqlExpression(expression))
                    for name, expression in spec.map.items()
                )
            )
        )
    nodes.extend(_step_to_node(step, index) for index, step in enumerate(spec.steps))
    nodes.append(WriteParquet(spec.output))
    return LogicalPlan(tuple(nodes))


def _step_to_node(step: Step, step_index: int) -> Operation:
    match step:
        case FilterStep(filter=predicate):
            return Filter(SqlExpression(predicate))
        case RenameStep(rename=columns):
            return Rename(tuple(ColumnRename(source, target) for source, target in columns.items()))
        case CastStep(cast=columns):
            return Cast(
                tuple(ColumnCast(column, data_type) for column, data_type in columns.items())
            )
        case SelectStep(select=columns):
            return Select(tuple(columns))
        case DedupStep(dedup=keys):
            return Deduplicate(tuple(keys))
        case DeriveStep(derive=expressions):
            return Derive(
                tuple(
                    NamedExpression(name, SqlExpression(expression))
                    for name, expression in expressions.items()
                )
            )
        case SqlStep(sql=expression):
            return RawSql(expression)
        case PythonUdfStep(
            python=target,
            args=args,
            output=output,
            arg_types=arg_types,
            return_type=return_type,
        ):
            return PythonUdf(
                target=target,
                args=tuple(args),
                output=output,
                arg_types=tuple(arg_types) if arg_types is not None else None,
                return_type=return_type,
                step_index=step_index,
            )
    raise AssertionError(f"unsupported transform step: {step!r}")  # pragma: no cover


class Portability(StrEnum):
    """Whether a plan feature can be rendered by the v1 portable contract."""

    PORTABLE = "portable"
    DUCKDB_ONLY = "duckdb_only"
    ENGINE_LOCAL = "engine_local"


@dataclass(frozen=True)
class PortabilityIssue:
    """One deterministic reason a plan cannot use a portable executor."""

    portability: Portability
    location: str
    message: str


@dataclass(frozen=True)
class PortabilityReport:
    """Portable-plan preflight result, suitable for an executor error message."""

    plan: LogicalPlan
    issues: tuple[PortabilityIssue, ...]

    @property
    def portability(self) -> Portability:
        if any(issue.portability is Portability.ENGINE_LOCAL for issue in self.issues):
            return Portability.ENGINE_LOCAL
        if self.issues:
            return Portability.DUCKDB_ONLY
        return Portability.PORTABLE

    @property
    def is_portable(self) -> bool:
        return not self.issues

    def require_portable(self) -> LogicalPlan:
        """Return the plan or reject it before it reaches a remote executor."""

        if not self.is_portable:
            raise NonPortablePlanError(self)
        return self.plan


class NonPortablePlanError(ValueError):
    """Raised when a transform asks for a portable executor but uses a local feature."""

    def __init__(self, report: PortabilityReport) -> None:
        self.report = report
        details = "\n".join(f"- {issue.location}: {issue.message}" for issue in report.issues)
        super().__init__("transform is not portable:\n" + details)


def portability_report(spec: TransformSpec | LogicalPlan) -> PortabilityReport:
    """Classify a spec or plan without changing local DuckDB execution behavior."""

    plan = build_logical_plan(spec) if isinstance(spec, TransformSpec) else spec
    issues: list[PortabilityIssue] = []
    map_offset = int(bool(plan.operations) and isinstance(plan.operations[0], Project))
    for operation_index, node in enumerate(plan.operations):
        location = _operation_location(node, operation_index - map_offset)
        match node:
            case Project(expressions=expressions):
                for expression in expressions:
                    issues.extend(
                        _expression_issues(expression.expression, f"map.{expression.name}")
                    )
            case Filter(predicate=predicate):
                issues.extend(_expression_issues(predicate, location))
            case Derive(expressions=expressions):
                for expression in expressions:
                    issues.extend(
                        _expression_issues(expression.expression, f"{location}.{expression.name}")
                    )
            case Cast(columns=columns):
                for column in columns:
                    if column.data_type.lower() not in _PORTABLE_CAST_TYPES:
                        issues.append(
                            PortabilityIssue(
                                Portability.DUCKDB_ONLY,
                                f"{location}.{column.column}",
                                "cast type "
                                f"{column.data_type!r} is not in LogicalPlan v1's ANSI subset",
                            )
                        )
            case RawSql():
                issues.append(
                    PortabilityIssue(
                        Portability.ENGINE_LOCAL,
                        location,
                        "raw sql steps are DuckDB-local escape hatches",
                    )
                )
            case PythonUdf(target=target):
                issues.append(
                    PortabilityIssue(
                        Portability.ENGINE_LOCAL,
                        location,
                        f"Python UDF {target!r} can only run in the local DuckDB executor",
                    )
                )
            case Rename() | Deduplicate() | Select():
                pass
    return PortabilityReport(plan=plan, issues=tuple(issues))


def preflight_portable(spec: TransformSpec | LogicalPlan) -> LogicalPlan:
    """Build and require the portable subset for a remote executor submission."""

    return portability_report(spec).require_portable()


def _operation_location(node: Operation, operation_index: int) -> str:
    step_index = node.step_index if isinstance(node, PythonUdf) else operation_index
    return f"step {step_index} ({_node_name(node)})"


def _node_name(node: Operation) -> str:
    names: dict[type[object], str] = {
        Project: "map",
        Rename: "rename",
        Cast: "cast",
        Filter: "filter",
        Derive: "derive",
        Deduplicate: "dedup",
        Select: "select",
        RawSql: "sql",
        PythonUdf: "python",
    }
    return names[type(node)]


# The renderer-independent v1 expression contract is intentionally small.  It
# covers ordinary predicates and derived values while rejecting functions whose
# semantics or names are DuckDB-specific before a remote job is submitted.
_ANSI_FUNCTIONS = frozenset(
    {
        "abs",
        "cast",
        "ceil",
        "ceiling",
        "char_length",
        "coalesce",
        "concat",
        "current_date",
        "current_time",
        "current_timestamp",
        "extract",
        "floor",
        "length",
        "lower",
        "mod",
        "nullif",
        "position",
        "power",
        "replace",
        "sqrt",
        "substring",
        "translate",
        "trim",
        "upper",
    }
)
_PORTABLE_CAST_TYPES = frozenset(
    {
        "bigint",
        "boolean",
        "date",
        "decimal",
        "double",
        "integer",
        "timestamp",
        "varchar",
    }
)
_DUCKDB_OPERATORS = ("::", "->>", "->", "~~", "!~~")
_FORBIDDEN_SQL_WORDS = re.compile(
    r"\b(?:alter|attach|copy|create|delete|drop|insert|join|merge|pragma|select|update)\b",
    re.IGNORECASE,
)
_FUNCTION_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$]*)\s*\(")
_SQL_LITERALS_AND_IDENTIFIERS = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")


def _expression_issues(expression: SqlExpression, location: str) -> list[PortabilityIssue]:
    source = expression.source
    inspected = _SQL_LITERALS_AND_IDENTIFIERS.sub(" ", source)
    issues: list[PortabilityIssue] = []
    if ";" in inspected or "--" in inspected or "/*" in inspected:
        issues.append(
            PortabilityIssue(
                Portability.DUCKDB_ONLY,
                location,
                "expression is not a single ANSI expression",
            )
        )
        return issues
    if _FORBIDDEN_SQL_WORDS.search(inspected):
        issues.append(
            PortabilityIssue(
                Portability.DUCKDB_ONLY,
                location,
                "expression contains a SQL query clause instead of an ANSI expression",
            )
        )
    for operator in _DUCKDB_OPERATORS:
        if operator in inspected:
            issues.append(
                PortabilityIssue(
                    Portability.DUCKDB_ONLY,
                    location,
                    f"operator {operator!r} is DuckDB-specific",
                )
            )
            break
    functions = {match.group(1).lower() for match in _FUNCTION_CALL.finditer(inspected)}
    for function in sorted(functions - _ANSI_FUNCTIONS):
        issues.append(
            PortabilityIssue(
                Portability.DUCKDB_ONLY,
                location,
                f"function {function!r} is not in LogicalPlan v1's ANSI subset",
            )
        )
    return issues


__all__ = [
    "PLAN_VERSION",
    "Cast",
    "ColumnCast",
    "ColumnRename",
    "Deduplicate",
    "Derive",
    "Filter",
    "LogicalPlan",
    "NamedExpression",
    "NonPortablePlanError",
    "Portability",
    "PortabilityIssue",
    "PortabilityReport",
    "Project",
    "PythonUdf",
    "RawSql",
    "ReadParquet",
    "Rename",
    "Select",
    "SqlExpression",
    "WriteParquet",
    "build_logical_plan",
    "portability_report",
    "preflight_portable",
]
