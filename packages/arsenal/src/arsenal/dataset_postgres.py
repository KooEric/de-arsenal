"""Publish an `arsenal dataset` snapshot to a Postgres table.

The whole snapshot replaces the table's rows in one Postgres transaction. Rows go to a
temporary table first, and the target is changed with DELETE + INSERT, not dropped and
recreated. Views, grants and comments on the table survive a publication, and readers
see the previous snapshot until the transaction commits.

The schema is never created here: it belongs to whoever owns the database. A target
table that already exists must have exactly the snapshot's columns and types.
"""

import os
from dataclasses import dataclass
from typing import Any, cast

import duckdb
import pyarrow as pa

from arsenal_core.errors import FatalError

TEMP_TABLE = "arsenal_publish"
TARGET_HELP = "--postgres-table schema.table와 --postgres-dsn-env 환경변수_이름을 함께 지정하세요."


@dataclass(frozen=True)
class PostgresTarget:
    schema: str
    table: str
    dsn_env: str

    @property
    def name(self) -> str:
        return f"{self.schema}.{self.table}"


def parse_target(table: str | None, dsn_env: str | None) -> PostgresTarget | None:
    if table is None and dsn_env is None:
        return None
    if not table or not dsn_env:
        raise FatalError(TARGET_HELP)
    schema, dot, name = table.partition(".")
    if not dot or not schema or not name or "." in name:
        raise FatalError(f"Postgres 대상은 schema.table 형식이어야 합니다: {table}. {TARGET_HELP}")
    return PostgresTarget(schema=schema, table=name, dsn_env=dsn_env)


def _columns(con: Any, relation: str) -> list[tuple[str, str]]:
    rows = con.execute(
        "SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
        [relation],
    ).fetchall()
    return [(str(name), str(kind)) for name, kind in rows]


def _replace(con: Any, target: PostgresTarget, rows: pa.Table) -> None:
    from psycopg import sql

    from pugio.sinks.postgres import pg_column_type

    if con.execute("SELECT to_regnamespace(%s)", [target.schema]).fetchone()[0] is None:
        raise FatalError(
            f"Postgres 스키마가 없습니다: {target.schema}. "
            "DB 관리자에게 스키마 생성과 쓰기 권한을 요청하세요."
        )
    temp = sql.Identifier(TEMP_TABLE)
    con.execute(
        sql.SQL("CREATE TEMP TABLE {} ({}) ON COMMIT DROP").format(
            temp,
            sql.SQL(", ").join(
                sql.SQL("{} {}").format(sql.Identifier(f.name), pg_column_type(f))
                for f in rows.schema
            ),
        )
    )
    names = sql.SQL(", ").join(sql.Identifier(f.name) for f in rows.schema)
    with con.cursor().copy(sql.SQL("COPY {} ({}) FROM STDIN").format(temp, names)) as copy:
        for row in rows.to_pylist():
            copy.write_row(list(row.values()))

    qualified = sql.Identifier(target.schema, target.table)
    exists = con.execute("SELECT to_regclass(%s)", [qualified.as_string(con)]).fetchone()[0]
    if exists is None:
        con.execute(sql.SQL("CREATE TABLE {} (LIKE {})").format(qualified, temp))
    else:
        current = _columns(con, qualified.as_string(con))
        incoming = _columns(con, TEMP_TABLE)
        if current != incoming:
            raise FatalError(
                f"Postgres 테이블 {target.name}의 열 구성이 게시할 데이터와 다릅니다. "
                f"테이블: {current}, 데이터: {incoming}"
            )
        con.execute(sql.SQL("DELETE FROM {}").format(qualified))
    con.execute(sql.SQL("INSERT INTO {} SELECT * FROM {}").format(qualified, temp))


def publish(db: duckdb.DuckDBPyConnection, table: str, target: PostgresTarget) -> dict[str, Any]:
    """Replace ``target``'s rows with ``table`` from ``db``. Raises FatalError on failure."""
    dsn = os.environ.get(target.dsn_env)
    if not dsn:
        raise FatalError(f"Postgres 접속 정보 환경변수가 비어 있습니다: {target.dsn_env}")
    try:
        import psycopg
    except ImportError as e:
        raise FatalError(
            "Postgres로 게시하려면 postgres 추가 기능을 설치하세요: "
            'uv tool install "de-arsenal[postgres]"'
        ) from e
    rows = cast(pa.Table, db.execute(f"FROM {table}").to_arrow_table())  # pyright: ignore[reportUnknownMemberType]
    try:
        with psycopg.connect(dsn) as con, con.transaction():
            _replace(con, target, rows)
    except psycopg.Error as e:
        message = str(e).replace(dsn, "<redacted-dsn>").strip()
        raise FatalError(f"Postgres 게시에 실패했습니다 ({target.name}): {message}") from e
    return {"table": target.name, "rows": rows.num_rows}
