"""Pydantic YAML models for a Pilum dispatch."""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

# One pass over a template: `${ENV_VAR}` first, then `{column}`. Matching both in
# a single expression means a substituted value is never scanned again, so a
# record value that reads "${TOKEN}" stays literal text.
PLACEHOLDER = re.compile(r"\$\{(\w+)\}|\{(\w+)\}")


def record_fields(template: str) -> set[str]:
    """Column names a template refers to as ``{column}``."""

    return {match.group(2) for match in PLACEHOLDER.finditer(template) if match.group(2)}


def body_fields(node: Any) -> set[str]:
    """Column names referenced anywhere in a ``body`` structure."""

    if isinstance(node, str):
        return record_fields(node)
    children: list[object] = []
    if isinstance(node, dict):
        children = list(cast(dict[object, object], node).values())
    elif isinstance(node, list):
        children = cast(list[object], node)
    found: set[str] = set()
    for child in children:
        found |= body_fields(child)
    return found


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceSpec(_Frozen):
    type: Literal["parquet", "query"]
    path: str | None = Field(
        default=None,
        description="`parquet`: a file, a glob, or a directory (read as `<dir>/**/*.parquet`).",
    )
    sql: str | None = Field(
        default=None,
        description="`query`: one DuckDB SELECT, e.g. over `read_parquet('data/marts/*.parquet')`.",
    )

    @model_validator(mode="after")
    def _one_input(self) -> SourceSpec:
        if self.type == "parquet" and (self.path is None or self.sql is not None):
            raise ValueError("parquet source takes `path` (and no `sql`)")
        if self.type == "query" and (self.sql is None or self.path is not None):
            raise ValueError("query source takes `sql` (and no `path`)")
        return self


class DeleteSpec(_Frozen):
    """The request sent for a record that left the source (`mode: mirror`)."""

    url: str = Field(description="May use `${ENV_VAR}` and `{column}` for key columns only.")
    method: Literal["DELETE", "POST", "PUT", "PATCH"] = "DELETE"


class DestinationSpec(_Frozen):
    type: Literal["http", "slack"]
    url: str = Field(
        description=(
            "Request URL. `${ENV_VAR}` is read from the environment when a request is "
            "sent and never stored; `{column}` is filled from the record, percent-encoded."
        )
    )
    method: Literal["POST", "PUT", "PATCH"] = "POST"
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Header values may use `${ENV_VAR}`, e.g. `Authorization: Bearer ${TOKEN}`.",
    )
    body: Any = Field(
        default=None,
        description=(
            "`http` only. JSON body template. A string that is exactly `{column}` becomes "
            "that column's typed value; other strings are formatted. Omit to send the "
            "whole record."
        ),
    )
    text: str | None = Field(
        default=None, description="`slack` only. Message text; `{column}` is filled in."
    )
    idempotency_header: str | None = Field(
        default=None,
        description=(
            "`http` only. Header that carries the dispatch id, e.g. `Idempotency-Key`. "
            "Without it (or if the endpoint ignores it) delivery is at-least-once."
        ),
    )
    delete: DeleteSpec | None = None
    rps: float | None = Field(default=None, gt=0, description="Requests per second ceiling.")
    timeout_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _shape_per_type(self) -> DestinationSpec:
        if self.type == "slack":
            if self.text is None:
                raise ValueError("slack destination requires `text`")
            if self.body is not None or self.delete is not None or self.idempotency_header:
                raise ValueError(
                    "slack destination takes `text` only (no body, delete or idempotency_header)"
                )
        elif self.text is not None:
            raise ValueError("`text` is for slack destinations; use `body` for http")
        return self


class RetrySpec(_Frozen):
    max_attempts: int = Field(default=5, ge=1, description="Tries per record within one run.")
    base_wait_seconds: float = Field(default=1.0, ge=0, description="Doubles per try, max 60 s.")


class DlqSpec(_Frozen):
    after_attempts: int = Field(
        default=5,
        ge=1,
        description=(
            "A record whose failed tries, summed over runs, reach this count is parked in "
            "the dead-letter queue. A 4xx rejection parks it immediately."
        ),
    )


class SafetySpec(_Frozen):
    max_first_dispatch: int = Field(
        default=1000,
        ge=0,
        description=(
            "With an empty ledger, refuse to send more than this many records unless "
            "`--confirm-initial-load` is given."
        ),
    )
    max_delete_fraction: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description=(
            "`mirror`: refuse a run that would delete more than this share of the ledger "
            "unless `--confirm-deletes` is given (an empty or mis-pointed source looks "
            "like 'everything was removed')."
        ),
    )
    max_consecutive_failures: int = Field(
        default=5,
        ge=1,
        description=(
            "Stop the run when this many records in a row exhaust their retries: the "
            "destination is down, and the records are not at fault."
        ),
    )


class DispatchSpec(_Frozen):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    source: SourceSpec
    key: list[str] = Field(min_length=1, description="Columns that identify one record.")
    mode: Literal["insert", "upsert", "mirror"] = Field(
        default="upsert",
        description=(
            "`insert`: new keys only. `upsert`: new and changed keys. `mirror`: also send "
            "`destination.delete` for keys that left the source."
        ),
    )
    destination: DestinationSpec
    retry: RetrySpec = Field(default_factory=RetrySpec)
    dlq: DlqSpec = Field(default_factory=DlqSpec)
    safety: SafetySpec = Field(default_factory=SafetySpec)
    state_dir: str = ".arsenal"

    @model_validator(mode="after")
    def _mirror_needs_delete(self) -> DispatchSpec:
        if len(set(self.key)) != len(self.key):
            raise ValueError("key columns must be distinct")
        delete = self.destination.delete
        if self.mode == "mirror" and delete is None:
            where = "slack cannot delete" if self.destination.type == "slack" else "add it"
            raise ValueError(f"mode `mirror` requires `destination.delete` ({where})")
        if delete is not None:
            extra = record_fields(delete.url) - set(self.key)
            if extra:
                raise ValueError(
                    f"destination.delete.url may only use key columns; not {sorted(extra)} "
                    "(a removed record's other values are no longer known)"
                )
        return self

    def referenced_fields(self) -> set[str]:
        """Every source column the spec needs: the key plus all template fields."""

        destination = self.destination
        fields = set(self.key) | record_fields(destination.url)
        fields |= body_fields(destination.body)
        if destination.text is not None:
            fields |= record_fields(destination.text)
        return fields
