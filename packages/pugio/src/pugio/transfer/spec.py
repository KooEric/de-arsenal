"""YAML spec for ``pugio transfer``: a query the source engine exports to S3, copied into Redshift.

The rows never pass through this process. Redshift runs ``UNLOAD`` and Hive runs
``INSERT OVERWRITE DIRECTORY`` straight to S3 as Parquet; the target Redshift then
loads those files with ``COPY``. This is the path for tables too large to stream.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arsenal_core.errors import FatalError
from arsenal_core.yaml_io import yaml_error_summary

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# A staging URL ends up inside quoted SQL literals in three dialects; keep it plain.
_S3_URL = re.compile(r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](/[A-Za-z0-9!_.*()=+-]+)*/?$")
_SETTING_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")
_SETTING_VALUE = re.compile(r"^[^;\n\r'\"`\\]*$")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _QuerySource(_Frozen):
    dsn_env: str
    query: str = Field(description="One SELECT. Its column order must match the target table.")

    @field_validator("query")
    @classmethod
    def _one_select(cls, value: str) -> str:
        """Drop trailing ``;``: the query is wrapped in a subquery (and in UNLOAD's quotes)."""
        query = value.strip()
        while query.endswith(";"):
            query = query[:-1].rstrip()
        if not query:
            raise ValueError("query is empty")
        return query


class RedshiftQuerySource(_QuerySource):
    """Source cluster runs ``UNLOAD (query) TO staging FORMAT AS PARQUET``."""

    engine: Literal["redshift"]
    dsn_env: str = Field(description="Holds `redshift://user:password@host:5439/database`.")
    iam_role_env: str = Field(description="Holds the IAM role ARN the source cluster unloads with.")
    max_file_size_mb: int | None = Field(default=None, ge=5, le=6200)


class HiveQuerySource(_QuerySource):
    """HiveServer2 runs ``INSERT OVERWRITE DIRECTORY staging STORED AS PARQUET query``."""

    engine: Literal["hive"]
    dsn_env: str = Field(description="Holds `hive://user:password@host:10000/database?auth=PLAIN`.")
    settings: dict[str, str] = Field(
        default_factory=lambda: dict[str, str](),
        description="Session settings sent as `SET key=value` first, e.g. `tez.queue.name`.",
    )
    write_scheme: Literal["s3", "s3a"] = Field(
        default="s3", description="URL scheme Hive writes with (`s3a` outside EMR)."
    )

    @field_validator("settings")
    @classmethod
    def _plain_settings(cls, value: dict[str, str]) -> dict[str, str]:
        for key, setting in value.items():
            if not _SETTING_KEY.fullmatch(key) or not _SETTING_VALUE.fullmatch(setting):
                raise ValueError(f"settings entry {key!r} has characters that are not allowed")
        return value


QuerySource = Annotated[RedshiftQuerySource | HiveQuerySource, Field(discriminator="engine")]


class TransferSplitSpec(_Frozen):
    """Half-open ranges ``[start, end)`` of one column; each range is one unit."""

    column: str
    start: dt.date | int
    end: dt.date | int
    every: Literal["day", "month"] | int = Field(
        description="`day` or `month` for dates, a positive step for integers."
    )

    @field_validator("column")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("column must be a plain identifier (letters, digits, _)")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> TransferSplitSpec:
        dates = isinstance(self.start, dt.date), isinstance(self.end, dt.date)
        if dates[0] != dates[1]:
            raise ValueError("start and end must be the same kind (both dates or both integers)")
        if self.start >= self.end:  # pyright: ignore[reportOperatorIssue]
            raise ValueError("start must be before end")
        if dates[0] and self.every not in ("day", "month"):
            raise ValueError("every must be 'day' or 'month' for a date split")
        if not dates[0] and (isinstance(self.every, str) or self.every <= 0):
            raise ValueError("every must be a positive integer for an integer split")
        return self


class RedshiftTargetSpec(_Frozen):
    """The table must exist; its column order is the order COPY loads Parquet columns in."""

    dsn_env: str = Field(description="Holds `redshift://user:password@host:5439/database`.")
    table: str = Field(description="`schema.table`, created beforehand with your dist/sort keys.")
    iam_role_env: str = Field(description="Holds the IAM role ARN the target cluster copies with.")
    mode: Literal["replace", "merge"] = Field(
        default="replace",
        description="`replace`: delete the unit's range, then insert. `merge`: delete rows "
        "with the same `merge_key`, then insert.",
    )
    merge_key: list[str] = Field(default_factory=lambda: list[str]())

    @field_validator("table")
    @classmethod
    def _table(cls, value: str) -> str:
        if not _TABLE.fullmatch(value):
            raise ValueError("table must look like schema.table (letters, digits, _)")
        return value

    @model_validator(mode="after")
    def _merge_key(self) -> RedshiftTargetSpec:
        if self.mode == "merge" and not self.merge_key:
            raise ValueError("mode 'merge' needs merge_key")
        for key in self.merge_key:
            if not IDENTIFIER.fullmatch(key):
                raise ValueError(f"merge_key column {key!r} must be a plain identifier")
        return self


class TransferSpec(_Frozen):
    name: str
    state_dir: str = ".arsenal"
    max_attempts: int = Field(default=3, ge=1, le=10)
    check_columns: bool = Field(
        default=True,
        description="Before exporting, compare the query's column names with the target "
        "table's, position by position, and stop if they differ.",
    )
    check_types: bool = Field(
        default=True,
        description="With check_columns, also compare type families (integer, float, "
        "decimal, string, boolean, date, timestamp, timestamptz) at each position.",
    )
    source: QuerySource
    split: TransferSplitSpec | None = None
    staging_path: str = Field(description="`s3://bucket/prefix`; each unit gets its own folder.")
    target: RedshiftTargetSpec

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.fullmatch(value):
            raise ValueError("name may contain letters, digits, '.', '_' and '-'")
        return value

    @field_validator("staging_path")
    @classmethod
    def _staging(cls, value: str) -> str:
        if not _S3_URL.fullmatch(value):
            raise ValueError("staging_path must be a plain s3://bucket/prefix URL")
        return value.rstrip("/")


def load_transfer(path: Path) -> TransferSpec:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FatalError(f"transfer spec file not found: {path}") from error
    except OSError as error:
        raise FatalError(f"cannot read transfer spec {path}: {error}") from error
    except yaml.YAMLError as error:
        raise FatalError(f"invalid YAML in {path}: {yaml_error_summary(error)}") from error
    try:
        return TransferSpec.model_validate(raw)
    except ValidationError as error:
        lines = [
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        ]
        raise FatalError(f"invalid transfer spec {path}:\n  " + "\n  ".join(lines)) from error
