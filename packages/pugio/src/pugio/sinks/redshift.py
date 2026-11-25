"""Amazon Redshift sink (experimental): staged delete + insert on ``merge_key``.

Redshift has no ``ON CONFLICT`` and does not enforce primary keys, so the
Postgres sink's upsert does not work there. Each batch is loaded into a
temporary table, the target rows with the same keys are deleted, and the staged
rows are inserted, all in one transaction. Writing the same batch again leaves
the same rows.

Two ways to fill the temporary table:

- ``load: insert``: multi-row ``INSERT`` statements over the connection.
- ``load: copy``: the batch is written as one Parquet file under
  ``staging_path`` (named after the unit, so a retry overwrites it) and loaded
  with ``COPY ... FORMAT AS PARQUET``.

Not verified against a live Redshift cluster. The ``insert`` path is exercised
against PostgreSQL, whose SQL it shares; the ``copy`` path is covered by tests
of the statements it issues.
"""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast
from urllib.parse import unquote, urlsplit

import pyarrow as pa

from arsenal_core.errors import ArsenalError, FatalError, RetryableError
from arsenal_core.spec.models import RedshiftSinkSpec
from arsenal_core.state import UnitSpec
from pugio.sinks.contracts import REDSHIFT_CAPABILITIES

_IAM_ROLE = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]+$")
_MAX_ROWS_PER_INSERT = 1000
_MAX_PARAMS_PER_INSERT = 30000  # the wire protocol allows 32767 bind parameters
_RETRYABLE_ERROR_NAMES = frozenset({"InterfaceError", "OperationalError"})
_FATAL_MARKERS = ("password authentication failed", "does not exist", "permission denied")


class _Cursor(Protocol):
    def execute(self, operation: str, args: Sequence[Any] = ...) -> Any: ...


class _Connection(Protocol):
    def cursor(self) -> Any: ...
    def commit(self) -> Any: ...
    def rollback(self) -> Any: ...
    def close(self) -> Any: ...


Connect = Callable[[], _Connection]
Stage = Callable[[UnitSpec, pa.RecordBatch], str]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_table(name: str) -> str:
    """``schema.table`` → ``"schema"."table"``."""

    parts = name.split(".")
    if not all(parts):
        raise FatalError(f"invalid redshift table name: {name!r}")
    return ".".join(quote_ident(part) for part in parts)


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def redshift_type(field: pa.Field) -> str:
    """Fixed whitelist. ``text`` is VARCHAR(256) on Redshift, so strings get the maximum."""

    t = field.type
    if pa.types.is_int64(t) or pa.types.is_uint32(t):
        return "BIGINT"
    if pa.types.is_int32(t) or pa.types.is_uint16(t):
        return "INTEGER"
    if pa.types.is_int16(t) or pa.types.is_int8(t) or pa.types.is_uint8(t):
        return "SMALLINT"
    if pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_null(t):
        return "VARCHAR(65535)"
    if pa.types.is_float64(t):
        return "DOUBLE PRECISION"
    if pa.types.is_float32(t):
        return "REAL"
    if pa.types.is_decimal(t):
        return f"DECIMAL({t.precision}, {t.scale})"
    if pa.types.is_boolean(t):
        return "BOOLEAN"
    if pa.types.is_date(t):
        return "DATE"
    if pa.types.is_timestamp(t):
        return "TIMESTAMP" if t.tz is None else "TIMESTAMPTZ"
    raise FatalError(f"unsupported arrow type for redshift sink: column={field.name!r} type={t!r}")


def keep_last_per_key(batch: pa.RecordBatch, merge_key: Sequence[str]) -> pa.RecordBatch:
    """One row per ``merge_key``, the last in batch order (same rule as the other sinks)."""

    missing = [key for key in merge_key if key not in batch.schema.names]
    if missing:
        raise FatalError(f"merge_key columns missing from the batch: {missing}")
    keys = zip(*(batch.column(name).to_pylist() for name in merge_key), strict=True)
    last: dict[tuple[Any, ...], int] = {}
    for index, key in enumerate(keys):
        last[key] = index
    if len(last) == batch.num_rows:
        return batch
    return batch.take(pa.array(sorted(last.values()), type=pa.int64()))


def parse_dsn(dsn: str) -> dict[str, Any]:
    """``redshift://user:password@host:5439/database`` → redshift_connector arguments."""

    try:
        parts = urlsplit(dsn)
        port = parts.port or 5439
    except ValueError as error:
        raise FatalError("redshift dsn is not a valid URL") from error
    database = parts.path.lstrip("/")
    if parts.scheme not in {"redshift", "postgresql", "postgres"} or not parts.hostname:
        raise FatalError("redshift dsn must look like redshift://user:password@host:5439/database")
    if not database or not parts.username:
        raise FatalError("redshift dsn needs a user and a database name")
    return {
        "host": parts.hostname,
        "port": port,
        "database": database,
        "user": unquote(parts.username),
        "password": unquote(parts.password or ""),
    }


def read_iam_role(env_name: str) -> str:
    """The IAM role ARN held by ``env_name``; anything else is a configuration error."""

    role = os.environ.get(env_name, "")
    if not _IAM_ROLE.fullmatch(role):
        raise FatalError(
            f"environment variable {env_name!r} must hold an IAM role ARN "
            "(arn:aws:iam::<account>:role/<name>)"
        )
    return role


def connect_redshift(dsn_env: str, purpose: str, secrets: list[str]) -> Any:
    """Open a ``redshift_connector`` connection.

    The DSN and password are appended to ``secrets`` before connecting, so the caller
    can mask them in the error of a failed connect too.
    """

    dsn = os.environ.get(dsn_env)
    if not dsn:
        raise FatalError(f"environment variable {dsn_env!r} is not set ({purpose} dsn)")
    arguments = parse_dsn(dsn)
    secrets.extend([dsn, arguments["password"]])
    try:
        driver = cast(Any, importlib.import_module("redshift_connector"))
    except ImportError as error:
        raise FatalError(
            f"{purpose} requires the 'redshift' extra: pip install pugio[redshift]"
        ) from error
    return driver.connect(**arguments)


def mask_secrets(message: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if len(secret) >= 4:
            message = message.replace(secret, "***")
    return message


def classify_driver_error(
    error: Exception,
    context: str,
    secrets: Sequence[str],
    retryable: frozenset[str] = _RETRYABLE_ERROR_NAMES,
) -> ArsenalError:
    """Driver errors by class name, so the driver stays an optional import."""

    message = mask_secrets(str(error), secrets)
    names = {cls.__name__ for cls in type(error).__mro__}
    temporary = bool(names & retryable) and not any(marker in message for marker in _FATAL_MARKERS)
    kind = RetryableError if temporary else FatalError
    return kind(f"{context}: {message}")


class RedshiftSink:
    capabilities = REDSHIFT_CAPABILITIES

    def __init__(
        self,
        spec: RedshiftSinkSpec,
        *,
        connect: Connect | None = None,
        stage: Stage | None = None,
    ) -> None:
        self._spec = spec
        self._connect = connect or self._default_connect
        self._stage = stage or self._default_stage
        self._target = quote_table(spec.table)
        self._secrets: list[str] = []

    # --- collaborators ----------------------------------------------------

    def _default_connect(self) -> _Connection:
        return connect_redshift(self._spec.dsn_env, "redshift sink", self._secrets)

    def _default_stage(self, unit: UnitSpec, batch: pa.RecordBatch) -> str:
        from pugio.sinks.object_storage import ObjectStorageParquetSink

        assert self._spec.staging_path is not None
        prefix = self._spec.staging_path.rstrip("/")
        ObjectStorageParquetSink(prefix).write(unit, batch)
        return f"{prefix}/{unit.unit_id}.parquet"

    def _iam_role(self) -> str:
        assert self._spec.iam_role_env is not None
        return read_iam_role(self._spec.iam_role_env)

    # --- write ------------------------------------------------------------

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        batch = keep_last_per_key(batch, self._spec.merge_key)
        columns = [(field.name, redshift_type(field)) for field in batch.schema]
        staged_uri: str | None = None
        role = ""
        if self._spec.load == "copy":
            role = self._iam_role()
            staged_uri = self._stage(unit, _without_null_columns(batch))
        try:
            con = self._connect()
        except ArsenalError:
            raise
        except Exception as error:
            raise self._classify(
                error, f"redshift connection failed for unit {unit.unit_id}"
            ) from error
        try:
            cursor = con.cursor()
            self._apply(cursor, unit, batch, columns, staged_uri, role)
            con.commit()
        except ArsenalError:
            quietly(con.rollback)
            raise
        except Exception as error:
            quietly(con.rollback)
            raise self._classify(error, f"redshift write failed for unit {unit.unit_id}") from error
        finally:
            quietly(con.close)

    def _apply(
        self,
        cursor: _Cursor,
        unit: UnitSpec,
        batch: pa.RecordBatch,
        columns: list[tuple[str, str]],
        staged_uri: str | None,
        role: str,
    ) -> None:
        stage = quote_ident(f"arsenal_stage_{unit.unit_id}")
        definition = ", ".join(f"{quote_ident(name)} {kind}" for name, kind in columns)
        names = ", ".join(quote_ident(name) for name, _ in columns)
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {self._target} ({definition})")
        cursor.execute(f"CREATE TEMP TABLE {stage} ({definition})")
        if staged_uri is not None:
            cursor.execute(
                f"COPY {stage} FROM {quote_literal(staged_uri)} "
                f"IAM_ROLE {quote_literal(role)} FORMAT AS PARQUET"
            )
        else:
            self._insert_rows(cursor, stage, names, batch)
        match = " AND ".join(
            f"{self._target}.{quote_ident(key)} = {stage}.{quote_ident(key)}"
            for key in self._spec.merge_key
        )
        cursor.execute(f"DELETE FROM {self._target} USING {stage} WHERE {match}")
        cursor.execute(f"INSERT INTO {self._target} ({names}) SELECT {names} FROM {stage}")
        cursor.execute(f"DROP TABLE {stage}")

    @staticmethod
    def _insert_rows(cursor: _Cursor, stage: str, names: str, batch: pa.RecordBatch) -> None:
        width = batch.num_columns
        chunk = max(1, min(_MAX_ROWS_PER_INSERT, _MAX_PARAMS_PER_INSERT // max(width, 1)))
        row_sql = "(" + ", ".join(["%s"] * width) + ")"
        rows = [tuple(row.values()) for row in batch.to_pylist()]
        for start in range(0, len(rows), chunk):
            part = rows[start : start + chunk]
            values = ", ".join([row_sql] * len(part))
            flat = [value for row in part for value in row]
            cursor.execute(f"INSERT INTO {stage} ({names}) VALUES {values}", flat)

    def _classify(self, error: Exception, context: str) -> ArsenalError:
        return classify_driver_error(error, context, self._secrets)


def _without_null_columns(batch: pa.RecordBatch) -> pa.RecordBatch:
    """An all-empty column has Arrow type ``null``; Parquet COPY needs a real type."""

    arrays = [
        column.cast(pa.string()) if pa.types.is_null(column.type) else column
        for column in batch.columns
    ]
    return pa.RecordBatch.from_arrays(arrays, names=batch.schema.names)


def quietly(action: Callable[[], Any]) -> None:
    try:
        action()
    except Exception:  # noqa: BLE001 - cleanup must not hide the original error
        return
