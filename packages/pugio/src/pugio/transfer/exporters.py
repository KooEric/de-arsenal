"""Source side: the engine itself writes one unit's query result to S3 as Parquet.

- Redshift: ``UNLOAD (...) TO 's3://.../<unit>/' FORMAT AS PARQUET CLEANPATH``. Slices
  write in parallel; ``CLEANPATH`` clears files left by an earlier attempt first.
- Hive: ``INSERT OVERWRITE DIRECTORY '...' STORED AS PARQUET SELECT ...``. The
  directory is replaced, so a retry never leaves a previous attempt's files behind.

Both are re-runnable: exporting a unit again replaces its folder.
"""

from __future__ import annotations

import functools
import importlib
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol, TypeVar, cast
from urllib.parse import parse_qsl, unquote, urlsplit

from arsenal_core.errors import ArsenalError, FatalError, RetryableError
from arsenal_core.retry import DEFAULT_BASE_WAIT, with_retry
from pugio.sinks.redshift import (
    classify_driver_error,
    connect_redshift,
    quote_literal,
    read_iam_role,
)
from pugio.transfer._db import Connect, first_int, run_statements
from pugio.transfer.columns import Column, from_hive, from_redshift_oid
from pugio.transfer.spec import HiveQuerySource, RedshiftQuerySource
from pugio.transfer.units import TransferUnit

T = TypeVar("T")
_LIST_ATTEMPTS = 3
_HIVE_AUTH = frozenset({"NOSASL", "PLAIN", "LDAP", "GSSAPI", "JWT"})
_HIVE_OPTIONS = frozenset({"auth", "kerberos_service_name", "use_ssl"})
# impyla / thrift class names for a dropped or refused connection.
_HIVE_RETRYABLE = frozenset(
    {"OperationalError", "TTransportException", "ConnectionError", "TimeoutError"}
)


@dataclass(frozen=True)
class ExportResult:
    rows: int | None  # None when the engine does not report it (Hive)
    empty: bool  # no data files: the load step must not COPY


class Exporter(Protocol):
    def columns(self) -> list[Column]: ...
    def export(self, unit: TransferUnit) -> ExportResult: ...


def columns_query(query: str) -> str:
    """Returns no rows; the cursor description carries the result's column names."""

    return f"SELECT * FROM (\n{query}\n) arsenal_cols LIMIT 0"


def description_columns(cursor: Any, make: Callable[[str, object], Column]) -> list[Column]:
    # Hive names result columns `<alias>.<column>` (hive.resultset.use.unique.column.names).
    described: list[Sequence[Any]] = list(cursor.description or [])
    return [make(str(column[0]).rsplit(".", 1)[-1], column[1]) for column in described]


ListFiles = Callable[[str], int]


def unload_literal(query: str) -> str:
    """UNLOAD takes the query as a string literal, where Redshift reads ``\\`` as an escape."""

    return "'" + query.replace("\\", "\\\\").replace("'", "''") + "'"


class RedshiftUnloadExporter:
    def __init__(
        self,
        source: RedshiftQuerySource,
        *,
        connect: Connect | None = None,
        secrets: Sequence[str] = (),
    ) -> None:
        self._source = source
        self._connect = connect
        self._secrets = list(secrets)

    def _open(self) -> Any:
        if self._connect is not None:
            return self._connect()
        return connect_redshift(self._source.dsn_env, "transfer source", self._secrets)

    def statement(self, unit: TransferUnit, role: str) -> str:
        size = self._source.max_file_size_mb
        return (
            f"UNLOAD ({unload_literal(unit.query(self._source.query))}) "
            f"TO {quote_literal(unit.uri)} IAM_ROLE {quote_literal(role)} "
            "FORMAT AS PARQUET CLEANPATH" + (f" MAXFILESIZE {size} MB" if size else "")
        )

    def columns(self) -> list[Column]:
        def work(cursor: Any) -> list[Column]:
            cursor.execute(columns_query(self._source.query))
            return description_columns(cursor, from_redshift_oid)

        def fail(error: Exception) -> ArsenalError:
            context = "reading the query's columns on the source redshift failed"
            return classify_driver_error(error, context, self._secrets)

        return run_statements(self._open, work, fail=fail)

    def export(self, unit: TransferUnit) -> ExportResult:
        statement = self.statement(unit, read_iam_role(self._source.iam_role_env))

        def work(cursor: Any) -> int:
            cursor.execute(statement)
            cursor.execute("SELECT pg_last_unload_count()")
            return first_int(cursor)

        def fail(error: Exception) -> ArsenalError:
            context = f"redshift unload failed for unit {unit.unit.unit_key}"
            return classify_driver_error(error, context, self._secrets)

        rows = run_statements(self._open, work, fail=fail)
        return ExportResult(rows=rows, empty=rows == 0)


def parse_hive_dsn(dsn: str) -> dict[str, Any]:
    """``hive://user:password@host:10000/database?auth=PLAIN`` → impyla ``connect`` arguments."""

    try:
        parts = urlsplit(dsn)
        port = parts.port or 10000
    except ValueError as error:
        raise FatalError("hive dsn is not a valid URL") from error
    if parts.scheme != "hive" or not parts.hostname:
        raise FatalError("hive dsn must look like hive://user:password@host:10000/database")
    options = dict(parse_qsl(parts.query))
    unknown = sorted(set(options) - _HIVE_OPTIONS)
    if unknown:
        raise FatalError(f"hive dsn has unknown options: {', '.join(unknown)}")
    auth = options.get("auth", "PLAIN").upper()
    if auth not in _HIVE_AUTH:
        raise FatalError(f"hive dsn auth must be one of {', '.join(sorted(_HIVE_AUTH))}")
    arguments: dict[str, Any] = {
        "host": parts.hostname,
        "port": port,
        "database": parts.path.strip("/") or "default",
        "auth_mechanism": auth,
    }
    if parts.username:
        arguments["user"] = unquote(parts.username)
    if parts.password:
        arguments["password"] = unquote(parts.password)
    if "kerberos_service_name" in options:
        arguments["kerberos_service_name"] = options["kerberos_service_name"]
    if "use_ssl" in options:
        arguments["use_ssl"] = options["use_ssl"].lower() in {"1", "true", "yes"}
    return arguments


def count_s3_files(uri: str) -> int:
    """Non-empty data files under ``uri`` (names starting with ``_`` or ``.`` are not data)."""

    # pyarrow.fs ships no usable type information.
    fs = cast(Any, importlib.import_module("pyarrow.fs"))
    try:
        filesystem, path = fs.FileSystem.from_uri(uri)
        infos = filesystem.get_file_info(
            fs.FileSelector(path, allow_not_found=True, recursive=True)
        )
    except OSError as error:
        raise RetryableError(f"cannot list {uri}: {error}") from error
    return sum(
        1
        for info in infos
        if info.type == fs.FileType.File
        and (info.size or 0) > 0
        and not PurePosixPath(info.path).name.startswith(("_", "."))
    )


class HiveExporter:
    def __init__(
        self,
        source: HiveQuerySource,
        *,
        connect: Connect | None = None,
        list_files: ListFiles = count_s3_files,
        list_retry_wait: float = DEFAULT_BASE_WAIT,
        secrets: Sequence[str] = (),
    ) -> None:
        self._source = source
        self._connect = connect
        self._list_files = list_files
        self._list_retry_wait = list_retry_wait
        self._secrets = list(secrets)

    def _open(self) -> Any:
        if self._connect is not None:
            return self._connect()
        dsn = os.environ.get(self._source.dsn_env)
        if not dsn:
            raise FatalError(
                f"environment variable {self._source.dsn_env!r} is not set (hive source dsn)"
            )
        arguments = parse_hive_dsn(dsn)
        self._secrets.extend([dsn, str(arguments.get("password", ""))])
        try:
            dbapi = cast(Any, importlib.import_module("impala.dbapi"))
        except ImportError as error:
            raise FatalError(
                "the hive source requires the 'hive' extra: pip install pugio[hive]"
            ) from error
        return dbapi.connect(**arguments)

    def _settings(self) -> list[str]:
        return [f"SET {key}={value}" for key, value in self._source.settings.items()]

    def statements(self, unit: TransferUnit) -> list[str]:
        directory = unit.uri
        if self._source.write_scheme == "s3a":
            directory = "s3a://" + directory.removeprefix("s3://")
        # The URL is validated to plain characters, so single quotes need no escaping.
        return [
            *self._settings(),
            f"INSERT OVERWRITE DIRECTORY '{directory}' STORED AS PARQUET "
            f"{unit.query(self._source.query)}",
        ]

    def _query(self, sql: str, read: Callable[[Any], T], context: str) -> T:
        """One statement after the session settings, so it runs in the same queue."""

        def work(cursor: Any) -> T:
            for setting in self._settings():
                cursor.execute(setting)
            cursor.execute(sql)
            return read(cursor)

        return run_statements(self._open, work, fail=self._fail(context), transactional=False)

    def columns(self) -> list[Column]:
        return self._query(
            columns_query(self._source.query),
            lambda cursor: description_columns(cursor, from_hive),
            "reading the query's columns on hive failed",
        )

    def export(self, unit: TransferUnit) -> ExportResult:
        statements = self.statements(unit)

        def work(cursor: Any) -> None:
            for statement in statements:
                cursor.execute(statement)

        context = f"hive export failed for unit {unit.unit.unit_key}"
        run_statements(self._open, work, fail=self._fail(context), transactional=False)
        if self._count_files(unit) > 0:
            return ExportResult(rows=None, empty=False)
        # No files can also mean Hive wrote somewhere else (scheme or bucket alias). Under
        # `replace` an empty unit clears its range in the target, so confirm it is empty.
        probe = f"SELECT 1 FROM (\n{unit.query(self._source.query)}\n) arsenal_probe LIMIT 1"

        def has_rows(cursor: Any) -> bool:
            return cursor.fetchone() is not None

        if self._query(probe, has_rows, context):
            raise FatalError(
                f"hive export for unit {unit.unit.unit_key} returned rows but no data files "
                f"were found under {unit.uri}; check source.write_scheme and that Hive "
                "writes to staging_path"
            )
        return ExportResult(rows=None, empty=True)

    def _fail(self, context: str) -> Callable[[Exception], ArsenalError]:
        def fail(error: Exception) -> ArsenalError:
            return classify_driver_error(error, context, self._secrets, _HIVE_RETRYABLE)

        return fail

    def _count_files(self, unit: TransferUnit) -> int:
        """Retried on its own: a failed listing must not rerun the Hive query."""

        try:
            return with_retry(
                functools.partial(self._list_files, unit.uri),
                max_attempts=_LIST_ATTEMPTS,
                base_wait=self._list_retry_wait,
            )
        except RetryableError as error:
            raise FatalError(
                f"cannot list {unit.uri} after the hive export: {error}. The export is done; "
                "fix S3 access, then rerun with --reexport"
            ) from error
