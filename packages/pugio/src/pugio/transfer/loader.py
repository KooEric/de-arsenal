"""Target side: Redshift copies one unit's exported Parquet files into the table.

All in one transaction, so a failure leaves the target as it was:

    CREATE TEMP TABLE stage (LIKE target)
    COPY stage FROM 's3://.../<unit>/' IAM_ROLE ... FORMAT AS PARQUET
    DELETE FROM target WHERE <unit range>        -- replace
    DELETE FROM target USING stage WHERE <keys>  -- merge
    INSERT INTO target SELECT * FROM stage

Loading the same unit again gives the same rows. Parquet columns load by position,
so the query's column order must match the target table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from arsenal_core.errors import ArsenalError
from pugio.sinks.redshift import (
    classify_driver_error,
    connect_redshift,
    quote_ident,
    quote_literal,
    quote_table,
    read_iam_role,
)
from pugio.transfer._db import Connect, first_int, run_statements
from pugio.transfer.columns import Column, from_redshift_catalog
from pugio.transfer.spec import RedshiftTargetSpec
from pugio.transfer.units import TransferUnit


class RedshiftCopyLoader:
    def __init__(
        self,
        target: RedshiftTargetSpec,
        *,
        connect: Connect | None = None,
        secrets: Sequence[str] = (),
    ) -> None:
        self._target = target
        self._table = quote_table(target.table)
        self._connect = connect
        self._secrets = list(secrets)

    def _open(self) -> Any:
        if self._connect is not None:
            return self._connect()
        return connect_redshift(self._target.dsn_env, "transfer target", self._secrets)

    def _delete(self, unit: TransferUnit, stage: str) -> str:
        if self._target.mode == "merge":
            match = " AND ".join(
                f"{self._table}.{quote_ident(key)} = {stage}.{quote_ident(key)}"
                for key in self._target.merge_key
            )
            return f"DELETE FROM {self._table} USING {stage} WHERE {match}"
        where = f" WHERE {unit.predicate}" if unit.predicate else ""
        return f"DELETE FROM {self._table}{where}"

    def columns(self) -> list[Column]:
        """The target table's columns in ordinal order (empty if it does not exist)."""

        parts = self._target.table.split(".")
        name = parts[-1]
        if len(parts) > 1:  # schema.table or database.schema.table
            schema_sql, params = "%s", (parts[-2], name)
        else:
            schema_sql, params = "current_schema()", (name,)
        sql = (
            "SELECT column_name, data_type, character_maximum_length, numeric_precision, "
            "numeric_scale FROM information_schema.columns "
            f"WHERE table_schema = {schema_sql} AND table_name = %s ORDER BY ordinal_position"
        )

        def work(cursor: Any) -> list[Column]:
            cursor.execute(sql, params)
            rows: list[Sequence[Any]] = list(cursor.fetchall() or [])
            return [
                from_redshift_catalog(str(name), str(kind), length, precision, scale)
                for name, kind, length, precision, scale in rows
            ]

        def fail(error: Exception) -> ArsenalError:
            context = f"reading the columns of {self._target.table} failed"
            return classify_driver_error(error, context, self._secrets)

        return run_statements(self._open, work, fail=fail)

    def load(self, unit: TransferUnit, *, empty: bool) -> int:
        """Return the number of rows copied in (0 for an empty export)."""

        if empty and self._target.mode == "merge":
            return 0  # nothing to upsert; rows outside the export are never touched by merge
        role = "" if empty else read_iam_role(self._target.iam_role_env)
        stage = quote_ident(f"arsenal_stage_{unit.unit.unit_id}")

        def work(cursor: Any) -> int:
            if empty:  # replace with no source rows: the range is now empty in the target too
                cursor.execute(self._delete(unit, stage))
                return 0
            cursor.execute(f"CREATE TEMP TABLE {stage} (LIKE {self._table})")
            cursor.execute(
                f"COPY {stage} FROM {quote_literal(unit.uri)} "
                f"IAM_ROLE {quote_literal(role)} FORMAT AS PARQUET"
            )
            cursor.execute("SELECT pg_last_copy_count()")
            rows = first_int(cursor)
            cursor.execute(self._delete(unit, stage))
            cursor.execute(f"INSERT INTO {self._table} SELECT * FROM {stage}")
            cursor.execute(f"DROP TABLE {stage}")
            return rows

        def fail(error: Exception) -> ArsenalError:
            context = f"redshift copy failed for unit {unit.unit.unit_key}"
            return classify_driver_error(error, context, self._secrets)

        return run_statements(self._open, work, fail=fail)
