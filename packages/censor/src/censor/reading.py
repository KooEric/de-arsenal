"""What was learned about one data target before any check looks at it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Profile:
    rows: int
    null_counts: Mapping[str, int]
    key_null_rows: int | None = None
    duplicate_keys: int | None = None  # key values that occur more than once
    time_min: str | None = None
    time_max: str | None = None
    time_max_at: datetime | None = None


@dataclass(frozen=True)
class DataReading:
    label: str
    key: tuple[str, ...] = ()
    time_column: str | None = None
    profile: Profile | None = None
    skipped: str | None = None  # why nothing was profiled: the checks are na
    failure: str | None = None  # the profile query failed unexpectedly: one error check
    notice: str | None = None  # scale guidance for stderr and the report
    tool_error: str | None = None  # the target could not be read: exit code 2
    sample: str | None = None
    newest_file_at: datetime | None = None
