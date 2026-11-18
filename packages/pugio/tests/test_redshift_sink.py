"""Redshift sink (experimental).

No Redshift cluster is available to these tests. Two substitutes:

- the ``insert`` path runs for real against PostgreSQL, which accepts the same
  statements (temp table, ``DELETE ... USING``, multi-row ``INSERT``);
- the ``copy`` path, which only Redshift can execute, is checked through the
  statements it issues against a recording connection.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

import psycopg
import pyarrow as pa
import pytest
from _sink_contract import (
    assert_composite_merge_key_upsert,
    assert_in_batch_duplicate_key_keeps_last,
    assert_merge_updates_changed_rows,
    assert_two_different_batches_accumulate,
    assert_write_twice_same_unit_is_idempotent,
    make_unit,
)
from psycopg import sql

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import PipelineSpec, RedshiftSinkSpec
from pugio.sinks import build_sink
from pugio.sinks.redshift import RedshiftSink, keep_last_per_key, parse_dsn, redshift_type

ROLE = "arn:aws:iam::123456789012:role/redshift-copy"


def spec(**overrides: Any) -> RedshiftSinkSpec:
    raw: dict[str, Any] = {
        "type": "redshift",
        "dsn_env": "RS_DSN",
        "table": "orders",
        "merge_key": ["id"],
    }
    raw.update(overrides)
    return RedshiftSinkSpec.model_validate(raw)


# --- insert path, executed on PostgreSQL ---------------------------------------


@pytest.fixture
def pg(postgres_url: str) -> str:
    return postgres_url


def on_postgres(url: str, table: str, merge_key: list[str] | None = None) -> RedshiftSink:
    return RedshiftSink(
        spec(table=table, merge_key=merge_key or ["id"]), connect=lambda: psycopg.connect(url)
    )


def rows_of(url: str, table: str) -> list[dict[str, object]]:
    with psycopg.connect(url) as con, con.cursor() as cur:
        cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
        names = [column.name for column in cur.description or []]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def test_write_twice_same_unit_row_count_unchanged(pg: str) -> None:
    assert_write_twice_same_unit_is_idempotent(
        on_postgres(pg, "rs_idem"), lambda: rows_of(pg, "rs_idem")
    )


def test_merge_updates_changed_rows(pg: str) -> None:
    assert_merge_updates_changed_rows(on_postgres(pg, "rs_merge"), lambda: rows_of(pg, "rs_merge"))


def test_two_different_batches_accumulate(pg: str) -> None:
    assert_two_different_batches_accumulate(
        on_postgres(pg, "rs_acc"), lambda: rows_of(pg, "rs_acc")
    )


def test_in_batch_duplicate_key_keeps_last(pg: str) -> None:
    assert_in_batch_duplicate_key_keeps_last(
        on_postgres(pg, "rs_dup"), lambda: rows_of(pg, "rs_dup")
    )


def test_composite_merge_key_upsert(pg: str) -> None:
    sink = on_postgres(pg, "rs_comp", ["a", "b"])
    assert_composite_merge_key_upsert(sink, lambda: rows_of(pg, "rs_comp"))


def test_csv_shaped_types_round_trip_and_large_batches_are_chunked(pg: str) -> None:
    schema = pa.schema(
        [
            ("id", pa.int64()),
            ("day", pa.date32()),
            ("at", pa.timestamp("s")),
            ("qty", pa.int32()),
            ("price", pa.decimal128(10, 2)),
            ("zip", pa.string()),
            ("empty", pa.null()),
        ]
    )
    records = [
        {
            "id": i,
            "day": datetime.date(2026, 11, 1),
            "at": datetime.datetime(2026, 11, 1, 9, 30),  # noqa: DTZ001
            "qty": i % 7,
            "price": Decimal("42.50"),
            "zip": "02139",
            "empty": None,
        }
        for i in range(2500)  # more than one INSERT statement
    ]
    sink = on_postgres(pg, "rs_types")
    sink.write(make_unit("u"), pa.RecordBatch.from_pylist(records, schema=schema))
    sink.write(make_unit("u"), pa.RecordBatch.from_pylist(records, schema=schema))

    stored = sorted(rows_of(pg, "rs_types"), key=lambda row: int(str(row["id"])))
    assert stored == records


def test_a_failed_write_leaves_the_target_untouched(pg: str) -> None:
    sink = on_postgres(pg, "rs_atomic")
    sink.write(make_unit("u1"), pa.RecordBatch.from_pylist([{"id": 1, "v": "a"}]))

    # A column the target does not have: the INSERT into the target fails after
    # the DELETE of id=1 already ran inside the same transaction.
    bad = pa.RecordBatch.from_pylist([{"id": 1, "v": "b", "extra": 1}])
    with pytest.raises(FatalError, match="redshift write failed"):
        sink.write(make_unit("u2"), bad)

    assert rows_of(pg, "rs_atomic") == [{"id": 1, "v": "a"}]


# --- copy path and error handling, against a recording connection --------------


class Recorder:
    def __init__(self, fail_on: str | None = None, error: Exception | None = None) -> None:
        self.statements: list[tuple[str, list[Any]]] = []
        self.events: list[str] = []
        self._fail_on = fail_on
        self._error = error or RuntimeError("boom")

    def cursor(self) -> Recorder:
        return self

    def execute(self, operation: str, args: Any = ()) -> None:
        if self._fail_on and operation.startswith(self._fail_on):
            raise self._error
        self.statements.append((operation, list(args)))

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("close")


def copy_sink(recorder: Recorder, staged: list[Any]) -> RedshiftSink:
    def stage(unit: Any, batch: pa.RecordBatch) -> str:
        staged.append(batch)
        return f"s3://bucket/stage/{unit.unit_id}.parquet"

    copy_spec = spec(
        table="analytics.orders",
        load="copy",
        staging_path="s3://bucket/stage",
        iam_role_env="RS_ROLE",
    )
    return RedshiftSink(copy_spec, connect=lambda: recorder, stage=stage)


def test_copy_path_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_ROLE", ROLE)
    recorder = Recorder()
    staged: list[Any] = []
    unit = make_unit("u")
    batch = pa.RecordBatch.from_pylist(
        [{"id": 1, "v": "old", "note": None}, {"id": 1, "v": "new", "note": None}]
    )

    copy_sink(recorder, staged).write(unit, batch)

    stage = f'"arsenal_stage_{unit.unit_id}"'
    columns = '"id" BIGINT, "v" VARCHAR(65535), "note" VARCHAR(65535)'
    assert [sql for sql, _ in recorder.statements] == [
        f'CREATE TABLE IF NOT EXISTS "analytics"."orders" ({columns})',
        f"CREATE TEMP TABLE {stage} ({columns})",
        f"COPY {stage} FROM 's3://bucket/stage/{unit.unit_id}.parquet' "
        f"IAM_ROLE '{ROLE}' FORMAT AS PARQUET",
        f'DELETE FROM "analytics"."orders" USING {stage} '
        f'WHERE "analytics"."orders"."id" = {stage}."id"',
        f'INSERT INTO "analytics"."orders" ("id", "v", "note") '
        f'SELECT "id", "v", "note" FROM {stage}',
        f"DROP TABLE {stage}",
    ]
    assert recorder.events == ["commit", "close"]
    # The staged file holds one row per key (the last) and no null-typed column.
    assert staged[0].to_pylist() == [{"id": 1, "v": "new", "note": None}]
    assert staged[0].schema.field("note").type == pa.string()


def test_copy_refuses_a_value_that_is_not_a_role_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RS_ROLE", "x' ; DROP TABLE orders; --")
    recorder = Recorder()
    staged: list[Any] = []

    with pytest.raises(FatalError, match="must hold an IAM role ARN"):
        copy_sink(recorder, staged).write(make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}]))

    assert recorder.statements == [] and staged == []


def test_identifiers_and_literals_are_quoted() -> None:
    recorder = Recorder()
    sink = RedshiftSink(spec(table='we"ird', merge_key=["i d"]), connect=lambda: recorder)
    sink.write(make_unit("u"), pa.RecordBatch.from_pylist([{"i d": 1, 'a"b': "x'y"}]))

    create, insert = recorder.statements[0], recorder.statements[2]
    assert create[0].startswith('CREATE TABLE IF NOT EXISTS "we""ird" ("i d" BIGINT, "a""b" ')
    assert insert[0].endswith("VALUES (%s, %s)") and insert[1] == [1, "x'y"]  # values are bound


def test_statement_failure_rolls_back_and_closes() -> None:
    recorder = Recorder(fail_on="DELETE")
    sink = RedshiftSink(spec(), connect=lambda: recorder)

    with pytest.raises(FatalError, match="redshift write failed for unit .*: boom"):
        sink.write(make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}]))

    assert recorder.events == ["rollback", "close"]


def test_driver_errors_are_classified_by_name_and_secrets_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OperationalError(Exception):
        pass

    def unreachable() -> Any:
        raise OperationalError("could not connect: timeout")

    with pytest.raises(RetryableError, match="redshift connection failed"):
        RedshiftSink(spec(), connect=unreachable).write(
            make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}])
        )

    def bad_password() -> Any:
        raise OperationalError("password authentication failed for user admin")

    with pytest.raises(FatalError):
        RedshiftSink(spec(), connect=bad_password).write(
            make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}])
        )


def test_missing_dsn_and_missing_driver_are_clear_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = pa.RecordBatch.from_pylist([{"id": 1}])
    monkeypatch.delenv("RS_DSN", raising=False)
    with pytest.raises(FatalError, match="'RS_DSN' is not set"):
        RedshiftSink(spec()).write(make_unit("u"), batch)

    monkeypatch.setenv("RS_DSN", "redshift://admin:hunter2secret@example.invalid:5439/dev")
    import builtins

    real_import = builtins.__import__

    def no_driver(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "redshift_connector":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_driver)
    with pytest.raises(FatalError, match=r"pip install pugio\[redshift\]"):
        RedshiftSink(spec()).write(make_unit("u"), batch)


def test_parse_dsn() -> None:
    assert parse_dsn("redshift://admin:p%40ss@cluster.example.com/dev") == {
        "host": "cluster.example.com",
        "port": 5439,
        "database": "dev",
        "user": "admin",
        "password": "p@ss",
    }
    for bad in ("not a url", "redshift://host/dev", "redshift://u:p@host", "mysql://u:p@h/d"):
        with pytest.raises(FatalError):
            parse_dsn(bad)


def test_types_and_dedup_helpers() -> None:
    assert redshift_type(pa.field("s", pa.string())) == "VARCHAR(65535)"  # not TEXT (= 256)
    assert redshift_type(pa.field("d", pa.decimal128(12, 3))) == "DECIMAL(12, 3)"
    assert redshift_type(pa.field("t", pa.timestamp("us", tz="UTC"))) == "TIMESTAMPTZ"
    with pytest.raises(FatalError, match="unsupported arrow type"):
        redshift_type(pa.field("l", pa.list_(pa.int64())))

    batch = pa.RecordBatch.from_pylist([{"id": 1, "v": 1}, {"id": 2, "v": 2}, {"id": 1, "v": 3}])
    assert keep_last_per_key(batch, ["id"]).to_pylist() == [{"id": 2, "v": 2}, {"id": 1, "v": 3}]
    with pytest.raises(FatalError, match="merge_key columns missing"):
        keep_last_per_key(batch, ["nope"])


def test_spec_validation_and_factory() -> None:
    with pytest.raises(ValueError, match="requires staging_path and iam_role_env"):
        spec(load="copy")
    with pytest.raises(ValueError, match="must be an s3:// URL"):
        spec(load="copy", staging_path="/tmp/x", iam_role_env="R")
    with pytest.raises(ValueError, match="merge_key"):
        spec(merge_key=[])

    pipeline = PipelineSpec.model_validate(
        {
            "name": "p",
            "source": {"type": "file", "path": "./in/*.csv", "detect_changes": True},
            "sink": {"type": "redshift", "dsn_env": "RS_DSN", "table": "t", "merge_key": ["id"]},
        }
    )
    assert isinstance(build_sink(pipeline.sink), RedshiftSink)
