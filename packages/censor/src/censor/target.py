"""What is being audited: a project or a bare dataset, plus the clock and the scan options."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Final, Literal

from censor.context import ProjectContext
from censor.reading import DataReading

TargetKind = Literal["project", "files", "duckdb", "postgres"]
Tier = Literal["quick", "standard"]
ALL_KINDS: Final[frozenset[TargetKind]] = frozenset({"project", "files", "duckdb", "postgres"})
PROJECT_ONLY: Final[frozenset[TargetKind]] = frozenset({"project"})

DEFAULT_MEMORY_LIMIT: Final = "4GB"
DEFAULT_TIMEOUT_SECONDS: Final = 120.0


@dataclass(frozen=True)
class FilesTarget:
    pattern: str

    @property
    def label(self) -> str:
        return self.pattern


@dataclass(frozen=True)
class DuckDBTarget:
    path: Path
    table: str

    @property
    def label(self) -> str:
        return f"{self.path}:{self.table}"


@dataclass(frozen=True)
class PostgresTarget:
    table: str
    dsn_env: str

    @property
    def label(self) -> str:
        """The table and the variable name; never the DSN itself."""
        return f"postgres:{self.table}"


DataTarget = FilesTarget | DuckDBTarget | PostgresTarget


def kind_of(target: DataTarget) -> TargetKind:
    if isinstance(target, FilesTarget):
        return "files"
    return "duckdb" if isinstance(target, DuckDBTarget) else "postgres"


@dataclass(frozen=True)
class ScanOptions:
    sample_percent: float | None = None
    since: date | None = None
    full: bool = False
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def narrowed(self) -> bool:
        """The user chose how much to read, so the scale guard steps aside."""
        return self.full or self.sample_percent is not None or self.since is not None


@dataclass(frozen=True)
class AuditContext:
    kind: TargetKind
    root: Path  # project root, or the directory whose .arsenal/censor/ keeps the history
    now: datetime
    project: ProjectContext | None = None
    data: DataTarget | None = None
    key: tuple[str, ...] = ()
    time_column: str | None = None
    max_age_hours: float | None = None
    scan: ScanOptions = field(default_factory=ScanOptions)
    readings: tuple[DataReading, ...] = ()  # filled before the Standard checks run
