"""Postgres target: attached READ_ONLY through DuckDB, sized from the catalog before any scan."""

from __future__ import annotations

import os
import re
from typing import Final
from urllib.parse import quote, urlsplit

import duckdb

from arsenal_core.errors import FatalError
from censor.data import SAMPLE_SEED, QueryTimeout, TargetUnreadable, narrow, run_query
from censor.limits import Scale
from censor.target import PostgresTarget, ScanOptions
from gladius.compile.ident import quote_ident

_ATTACHED: Final = "src"
_IDENT: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_REDACTED: Final = "<redacted-dsn>"
DEFAULT_SCHEMA: Final = "public"
_SIZED_RELKINDS: Final = ("r", "m")  # tables and materialized views; others report no size


def _dsn(target: PostgresTarget) -> str:
    dsn = os.environ.get(target.dsn_env)
    if not dsn:
        raise FatalError(f"environment variable not set: {target.dsn_env}")
    return dsn


def precheck_postgres(target: PostgresTarget) -> None:
    split_table(target.table)
    _dsn(target)


def with_statement_timeout(dsn: str, seconds: float) -> str:
    """Add `statement_timeout` to a DSN (URL or keyword form); a DSN with its own options wins.

    A `SET statement_timeout` sent later does not stick: the scanner opens a new transaction per
    query (see the investigation notes in docs/plans/2026-11-26-censor-plan2-standard.md).
    """
    if "options=" in dsn:
        return dsn
    setting = f"-c statement_timeout={int(seconds * 1000)}"
    if "://" in dsn:
        return f"{dsn}{'&' if '?' in dsn else '?'}options={quote(setting)}"
    return f"{dsn} options='{setting}'"


def redact(text: str, target: PostgresTarget) -> str:
    """Remove the DSN, and the password inside it, from a message."""
    dsn = os.environ.get(target.dsn_env)
    if not dsn:
        return text
    secrets = [dsn]
    if "://" in dsn:
        password = urlsplit(dsn).password
        if password:
            secrets.append(password)
    else:
        secrets += re.findall(r"password\s*=\s*('(?:[^'\\]|\\.)*'|\S+)", dsn)
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, _REDACTED)
    return re.sub(r"options=\S*statement_timeout\S*", "", text)


def _connect_failure(message: str) -> str:
    """A fixed phrase for a connection error; the driver's text is not passed on."""
    lowered = message.lower()
    if "password authentication failed" in lowered or "pg_hba.conf" in lowered:
        return "authentication failed"
    if "does not exist" in lowered:
        return "the database or role does not exist"
    if "permission denied" in lowered:
        return "permission denied"
    return "could not connect to the server"


def split_table(table: str) -> tuple[str, str]:
    schema, _, name = table.rpartition(".")
    schema = schema or DEFAULT_SCHEMA
    if not _IDENT.fullmatch(schema) or not _IDENT.fullmatch(name):
        raise FatalError(f"invalid table name {table!r}: use `table` or `schema.table`")
    return schema, name


def _server_sql(sql: str) -> str:
    return f"postgres_query('{_ATTACHED}', '{sql.replace(chr(39), chr(39) * 2)}')"


def postgres_relation(
    con: duckdb.DuckDBPyConnection, target: PostgresTarget, scan: ScanOptions
) -> tuple[str, Scale]:
    schema, name = split_table(target.table)
    dsn = with_statement_timeout(_dsn(target), scan.timeout_seconds)
    try:
        con.execute("INSTALL postgres")
        con.execute("LOAD postgres")
        attach = dsn.replace("'", "''")
        con.execute(f"ATTACH '{attach}' AS {_ATTACHED} (TYPE POSTGRES, READ_ONLY)")
        qualified = f"{quote_ident(schema)}.{quote_ident(name)}"
        size = _server_sql(
            "SELECT c.reltuples::bigint, pg_total_relation_size(c.oid), c.relkind::text "
            "FROM pg_class c "
            f"WHERE c.oid = to_regclass('{qualified}')"
        )
        rows = run_query(con, f"SELECT * FROM {size}", scan.timeout_seconds)
    except duckdb.Error as e:
        if "statement timeout" in str(e):
            raise QueryTimeout(scan.timeout_seconds) from e
        reason = _connect_failure(str(e))
        raise TargetUnreadable(
            f"could not read {target.label}: {reason} (DSN from ${target.dsn_env})"
        ) from None  # the driver's own text names the host, port and user: never chain it
    if not rows:
        raise TargetUnreadable(f"no table {target.table} (DSN from ${target.dsn_env})")
    estimated, total_bytes, relkind = rows[0]
    if relkind not in _SIZED_RELKINDS:
        return f"{_ATTACHED}.{qualified}", Scale(unknown=True)
    # reltuples is -1 until the table is analysed; the size still bounds it.
    scale = Scale(
        bytes=int(total_bytes),
        estimated_rows=int(estimated) if estimated is not None and estimated >= 0 else None,
    )
    return f"{_ATTACHED}.{qualified}", scale


def narrow_postgres(
    relation: str,
    target: PostgresTarget,
    scan: ScanOptions,
    key: tuple[str, ...],
    time_column: str | None,
) -> tuple[str, str | None]:
    """`--sample` runs on the server (TABLESAMPLE), so only the sampled blocks cross the wire."""
    percent = scan.sample_percent
    if percent is None:
        return narrow(relation, scan, key, time_column)
    if scan.since is not None and time_column is None:
        raise FatalError("--since needs --time-column")
    schema, name = split_table(target.table)
    sql = (
        f"SELECT * FROM {quote_ident(schema)}.{quote_ident(name)} "
        f"TABLESAMPLE SYSTEM ({percent:g}) REPEATABLE ({SAMPLE_SEED})"
    )
    if scan.since is not None and time_column is not None:
        sql += f" WHERE {quote_ident(time_column)} >= TIMESTAMP '{scan.since.isoformat()}'"
    sample = f"{percent:g}% of the table blocks (TABLESAMPLE SYSTEM, seed {SAMPLE_SEED})"
    return _server_sql(sql), sample
