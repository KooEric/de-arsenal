"""Gladius — 변환·쿼리 (핵심 처리).

map/steps 선언 → SQL 컴파일 → DuckDB 벡터화 실행. 단일 노드 수백 GB.
구조: spec.py compile/ engine.py cli.py (docs/02-architecture.md)
"""

from importlib.metadata import PackageNotFoundError, version

from gladius.compile.transpiler import compile_sql
from gladius.plan import (
    LogicalPlan,
    NonPortablePlanError,
    Portability,
    PortabilityReport,
    build_logical_plan,
    portability_report,
    preflight_portable,
)

try:
    __version__ = version("de-gladius")
except PackageNotFoundError:
    __version__ = "0.5.0"


__all__ = [
    "LogicalPlan",
    "NonPortablePlanError",
    "Portability",
    "PortabilityReport",
    "build_logical_plan",
    "compile_sql",
    "portability_report",
    "preflight_portable",
]
