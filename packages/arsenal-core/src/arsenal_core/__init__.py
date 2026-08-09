"""arsenal-core — DE Arsenal 공통 신뢰성 코어.

모든 도구(pugio, gladius, …)의 유일한 공통 의존성.
구조: docs/02-architecture.md 참조.
"""

__version__ = "0.1.1"
from arsenal_core.schema import SchemaChange, SchemaDiff, compare_schema_snapshots

__all__ = ["SchemaChange", "SchemaDiff", "compare_schema_snapshots"]
