"""Recording stand-ins for Redshift / Hive DB-API connections (shared by transfer tests)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

Answer = Callable[[str], Any]


@dataclass
class RecordingCursor:
    log: list[str]
    answer: Answer
    columns: list[tuple[str, Any]]
    params: list[tuple[Any, ...]]
    _last: Any = None

    def execute(self, operation: str, args: Sequence[Any] = ()) -> None:
        self.log.append(" ".join(operation.split()))
        self.params.append(tuple(args))
        self._last = self.answer(operation)

    @property
    def description(self) -> list[tuple[Any, ...]]:
        return [(name, type_code, None, None, None, None, None) for name, type_code in self.columns]

    def fetchone(self) -> Any:
        return self._last

    def fetchall(self) -> Any:
        return self._last

    def close(self) -> None:
        return None


@dataclass
class RecordingConnection:
    answer: Answer = lambda _sql: None
    fail_on: str | None = None
    error: Exception | None = None
    # (name, type_code) pairs that `description` reports
    columns: list[tuple[str, Any]] = field(default_factory=lambda: [])
    log: list[str] = field(default_factory=lambda: [])
    params: list[tuple[Any, ...]] = field(default_factory=lambda: [])
    committed: int = 0
    rolled_back: int = 0
    closed: int = 0

    def cursor(self) -> RecordingCursor:
        def answer(sql: str) -> Any:
            if self.fail_on is not None and self.fail_on in sql:
                raise self.error or RuntimeError(f"failed on {self.fail_on}")
            return self.answer(sql)

        return RecordingCursor(self.log, answer, self.columns, self.params)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.closed += 1


def counts(unload: int = 10, copy: int = 10) -> Answer:
    """Answer ``pg_last_unload_count()`` / ``pg_last_copy_count()`` with fixed numbers."""

    def answer(sql: str) -> Any:
        if "pg_last_unload_count" in sql:
            return (unload,)
        if "pg_last_copy_count" in sql:
            return (copy,)
        return None

    return answer


class OperationalError(Exception):
    """Named like the DB-API class the transfer code treats as temporary."""
