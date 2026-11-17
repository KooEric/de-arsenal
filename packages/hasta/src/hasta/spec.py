"""Pydantic YAML models for Hasta REST microbatch and future outbox streams."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arsenal_core.spec.models import ContractSpec, SinkSpec, ValidateSpec
from hasta.model import BatchLimits


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RestPollCursorSpec(_Frozen):
    request_param: str
    response_path: str
    event_id_path: str


class RestPollSourceSpec(_Frozen):
    type: Literal["rest_poll"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    cursor: RestPollCursorSpec
    record_path: str | None = "data"
    limit_param: str | None = None


class DatabaseOutboxSourceSpec(_Frozen):
    """Postgres append-only outbox declaration for CDC current-state apply."""

    type: Literal["database_outbox"]
    dialect: Literal["postgres"]
    dsn_env: str
    table: str
    sequence_column: str = "sequence"
    event_id_column: str = "event_id"
    operation_column: str = "op"
    key_column: str | None = None
    key_columns: list[str] = Field(default_factory=list)
    payload_column: str = "payload"
    occurred_at_column: str = "occurred_at"
    schema_version_column: str | None = "schema_version"

    @field_validator(
        "table",
        "sequence_column",
        "event_id_column",
        "operation_column",
        "key_column",
        "payload_column",
        "occurred_at_column",
        "schema_version_column",
    )
    @classmethod
    def _identifier_is_non_empty(cls, value: str | None) -> str | None:
        if value is not None and (not value or any(not part for part in value.split("."))):
            raise ValueError("SQL identifiers must be non-empty dot-separated names")
        return value

    @field_validator("key_columns")
    @classmethod
    def _key_columns_are_non_empty_and_unique(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or len(set(value)) != len(value):
            raise ValueError("key_columns must contain unique non-empty names")
        return value

    @model_validator(mode="after")
    def _exactly_one_key_encoding(self) -> DatabaseOutboxSourceSpec:
        if bool(self.key_column) == bool(self.key_columns):
            raise ValueError("database_outbox requires exactly one of key_column or key_columns")
        return self


class TableCursorSourceSpec(_Frozen):
    """Validated declaration of a deliberately limited table-cursor snapshot.

    The v1 runner does not treat a table cursor as CDC: it has no way to
    observe hard deletes.  Keeping this model strict prevents a future source
    implementation from silently accepting unsafe timestamp scans.
    """

    type: Literal["database_table_cursor"]
    dialect: Literal["postgres"]
    dsn_env: str
    table: str
    cursor_column: str
    primary_key: list[str] = Field(min_length=1)
    cursor_timezone: str
    cursor_immutable_or_monotonic: bool

    @field_validator("table", "cursor_column")
    @classmethod
    def _table_cursor_identifier_is_non_empty(cls, value: str) -> str:
        if not value or any(not part for part in value.split(".")):
            raise ValueError("SQL identifiers must be non-empty dot-separated names")
        return value

    @field_validator("primary_key")
    @classmethod
    def _table_cursor_primary_key_is_tie_breaker(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or len(set(value)) != len(value):
            raise ValueError("primary_key must contain unique non-empty names for timestamp ties")
        return value

    @model_validator(mode="after")
    def _table_cursor_safety_contract(self) -> TableCursorSourceSpec:
        if self.cursor_timezone != "UTC":
            raise ValueError("database_table_cursor cursor_timezone must be UTC")
        if not self.cursor_immutable_or_monotonic:
            raise ValueError(
                "database_table_cursor cursor_column must be immutable or monotonically increasing"
            )
        import warnings

        warnings.warn(
            "database_table_cursor cannot observe hard deletes; "
            "use database_outbox when deletes matter",
            UserWarning,
            stacklevel=2,
        )
        return self


StreamSourceSpec = Annotated[
    RestPollSourceSpec | DatabaseOutboxSourceSpec | TableCursorSourceSpec,
    Field(discriminator="type"),
]


class BatchSpec(_Frozen):
    max_records: int = Field(default=10_000, gt=0)
    max_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_wait_seconds: float = Field(default=20, gt=0)

    def limits(self) -> BatchLimits:
        return BatchLimits(self.max_records, self.max_bytes, self.max_wait_seconds)


class EventTimeSpec(_Frozen):
    field: str
    allowed_lateness: str = "PT0S"
    on_late: Literal["drop", "quarantine", "append"] = "quarantine"


class CheckpointSpec(_Frozen):
    lease_ttl_seconds: float = Field(default=60, gt=0)


class StreamSettings(_Frozen):
    name: str
    poll_interval_seconds: float = Field(default=15, gt=0)
    batch: BatchSpec = Field(default_factory=BatchSpec)
    event_time: EventTimeSpec | None = None
    checkpoint: CheckpointSpec = Field(default_factory=CheckpointSpec)


class StreamSpec(_Frozen):
    """Top-level Hasta YAML model, deliberately separate from batch PipelineSpec."""

    name: str
    source: StreamSourceSpec
    sink: SinkSpec
    stream: StreamSettings
    state_dir: str = ".arsenal"
    validation: ValidateSpec | None = Field(default=None, alias="validate")
    contract: ContractSpec | None = None
    schema_drift: Literal["allow", "warn", "block"] = "allow"

    @field_validator("state_dir", mode="before")
    @classmethod
    def _coerce_state_dir_to_str(cls, value: object) -> object:
        return str(value) if isinstance(value, Path) else value

    @field_validator("name")
    @classmethod
    def _name_is_path_safe(cls, value: str) -> str:
        if not value or "/" in value or "\\" in value or ".." in value:
            raise ValueError("stream name must be non-empty and path-safe")
        return value

    @model_validator(mode="after")
    def _outbox_requires_cdc_capable_sink(self) -> StreamSpec:
        if self.source.type == "database_outbox" and self.sink.type == "parquet":
            raise ValueError("database_outbox requires a transactional CDC sink, not parquet")
        return self

    def revision_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)
