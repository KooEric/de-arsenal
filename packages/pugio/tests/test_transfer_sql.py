"""Statements `pugio transfer` sends to the source engine and to the target Redshift.

No Hive or Redshift is available to these tests; the connections record what they receive.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from _transfer_fakes import OperationalError, RecordingConnection, counts

from arsenal_core.errors import FatalError, RetryableError
from pugio.transfer.exporters import HiveExporter, RedshiftUnloadExporter, parse_hive_dsn
from pugio.transfer.loader import RedshiftCopyLoader
from pugio.transfer.spec import HiveQuerySource, RedshiftQuerySource, TransferSpec
from pugio.transfer.units import plan_units

DAY_RANGE = "d >= DATE '2026-01-01' AND d < DATE '2026-01-02'"
UNLOAD_ROLE = "arn:aws:iam::123456789012:role/unload"
COPY_ROLE = "arn:aws:iam::123456789012:role/copy"


@pytest.fixture(autouse=True)
def roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNLOAD_ROLE", UNLOAD_ROLE)
    monkeypatch.setenv("COPY_ROLE", COPY_ROLE)


def make_spec(source: dict[str, Any], **target: Any) -> TransferSpec:
    return TransferSpec.model_validate(
        {
            "name": "t",
            "source": source,
            "split": {"column": "d", "start": "2026-01-01", "end": "2026-01-02", "every": "day"},
            "staging_path": "s3://bucket/stage",
            "target": {"dsn_env": "DW", "table": "analytics.orders", "iam_role_env": "COPY_ROLE"}
            | target,
        }
    )


def redshift_source(spec: TransferSpec) -> RedshiftQuerySource:
    assert isinstance(spec.source, RedshiftQuerySource)
    return spec.source


def hive_source(spec: TransferSpec) -> HiveQuerySource:
    assert isinstance(spec.source, HiveQuerySource)
    return spec.source


REDSHIFT = {
    "engine": "redshift",
    "dsn_env": "SRC",
    "query": "SELECT id, d FROM sales.orders WHERE status = 'paid'",
    "iam_role_env": "UNLOAD_ROLE",
}
HIVE = {
    "engine": "hive",
    "dsn_env": "HIVE",
    "query": "SELECT id, d FROM sales.orders",
    "settings": {"tez.queue.name": "etl"},
    "write_scheme": "s3a",
}


# --- Redshift UNLOAD ---------------------------------------------------------


def test_unload_writes_parquet_to_the_unit_prefix_and_reads_the_row_count() -> None:
    spec = make_spec(REDSHIFT | {"max_file_size_mb": 256})
    [unit] = plan_units(spec)
    con = RecordingConnection(answer=counts(unload=42))
    result = RedshiftUnloadExporter(redshift_source(spec), connect=lambda: con).export(unit)

    assert result.rows == 42 and not result.empty
    unload, count = con.log
    assert unload == (
        "UNLOAD ('SELECT * FROM ( SELECT id, d FROM sales.orders WHERE status = ''paid'' ) "
        "arsenal_src WHERE d >= DATE ''2026-01-01'' AND d < DATE ''2026-01-02''') "
        f"TO '{unit.uri}' IAM_ROLE '{UNLOAD_ROLE}' FORMAT AS PARQUET CLEANPATH "
        "MAXFILESIZE 256 MB"
    )
    assert count == "SELECT pg_last_unload_count()"
    assert con.committed == 1 and con.closed == 1


def test_unload_doubles_backslashes_in_the_query_literal() -> None:
    spec = make_spec(REDSHIFT | {"query": r"SELECT id, d FROM t WHERE code ~ '\\d+'"})
    [unit] = plan_units(spec)
    con = RecordingConnection(answer=counts())
    RedshiftUnloadExporter(redshift_source(spec), connect=lambda: con).export(unit)
    assert r"code ~ ''\\\\d+''" in con.log[0]  # each backslash doubled inside the literal


def test_unload_of_zero_rows_is_reported_empty() -> None:
    spec = make_spec(REDSHIFT)
    [unit] = plan_units(spec)
    con = RecordingConnection(answer=counts(unload=0))
    assert RedshiftUnloadExporter(redshift_source(spec), connect=lambda: con).export(unit).empty


def test_unload_refuses_a_malformed_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNLOAD_ROLE", "admin")
    spec = make_spec(REDSHIFT)
    [unit] = plan_units(spec)
    with pytest.raises(FatalError, match="IAM role ARN"):
        RedshiftUnloadExporter(redshift_source(spec), connect=RecordingConnection).export(unit)


def test_unload_network_errors_are_retryable_and_roll_back() -> None:
    spec = make_spec(REDSHIFT)
    [unit] = plan_units(spec)
    con = RecordingConnection(fail_on="UNLOAD", error=OperationalError("connection reset"))
    with pytest.raises(RetryableError, match="redshift unload failed"):
        RedshiftUnloadExporter(redshift_source(spec), connect=lambda: con).export(unit)
    assert con.rolled_back == 1 and con.closed == 1


# --- Hive INSERT OVERWRITE DIRECTORY ------------------------------------------


def test_hive_applies_settings_and_writes_parquet_under_the_write_scheme() -> None:
    spec = make_spec(HIVE)
    [unit] = plan_units(spec)
    con = RecordingConnection()
    listed: list[str] = []

    def list_files(uri: str) -> int:
        listed.append(uri)
        return 3

    result = HiveExporter(hive_source(spec), connect=lambda: con, list_files=list_files).export(
        unit
    )

    assert con.log == [
        "SET tez.queue.name=etl",
        f"INSERT OVERWRITE DIRECTORY '{unit.uri.replace('s3://', 's3a://', 1)}' "
        "STORED AS PARQUET SELECT * FROM ( SELECT id, d FROM sales.orders ) arsenal_src "
        "WHERE d >= DATE '2026-01-01' AND d < DATE '2026-01-02'",
    ]
    assert listed == [unit.uri]  # listing uses the s3:// form Redshift COPY reads
    assert result.rows is None and not result.empty


def test_hive_export_without_files_is_empty() -> None:
    spec = make_spec(HIVE)
    [unit] = plan_units(spec)
    exporter = HiveExporter(hive_source(spec), connect=RecordingConnection, list_files=lambda _: 0)
    assert exporter.export(unit).empty


def test_hive_zero_files_with_rows_in_the_query_is_refused() -> None:
    spec = make_spec(HIVE)
    [unit] = plan_units(spec)
    con = RecordingConnection(answer=lambda sql: (1,) if "LIMIT 1" in sql else None)
    exporter = HiveExporter(hive_source(spec), connect=lambda: con, list_files=lambda _: 0)
    with pytest.raises(FatalError, match="no data files"):
        exporter.export(unit)
    assert "arsenal_probe LIMIT 1" in con.log[-1]
    assert con.log[-2] == "SET tez.queue.name=etl"  # the probe runs in the same queue


def test_hive_listing_is_retried_without_running_the_query_again() -> None:
    spec = make_spec(HIVE)
    [unit] = plan_units(spec)
    con = RecordingConnection()
    calls: list[int] = []

    def flaky(_uri: str) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RetryableError("slow down")
        return 2

    exporter = HiveExporter(
        hive_source(spec), connect=lambda: con, list_files=flaky, list_retry_wait=0.0
    )
    assert not exporter.export(unit).empty
    assert len(calls) == 2
    assert sum("INSERT OVERWRITE" in sql for sql in con.log) == 1


def test_hive_listing_that_keeps_failing_is_fatal() -> None:
    spec = make_spec(HIVE)
    [unit] = plan_units(spec)

    def broken(_uri: str) -> int:
        raise RetryableError("no credentials")

    exporter = HiveExporter(
        hive_source(spec), connect=RecordingConnection, list_files=broken, list_retry_wait=0.0
    )
    with pytest.raises(FatalError, match="cannot list"):
        exporter.export(unit)


def test_hive_query_errors_are_fatal() -> None:
    spec = make_spec(HIVE)
    [unit] = plan_units(spec)
    con = RecordingConnection(fail_on="INSERT", error=RuntimeError("SemanticException"))
    with pytest.raises(FatalError, match="hive export failed"):
        HiveExporter(hive_source(spec), connect=lambda: con, list_files=lambda _: 1).export(unit)


def test_parse_hive_dsn() -> None:
    assert parse_hive_dsn("hive://etl:p%40ss@hs2.internal:10001/sales?auth=LDAP") == {
        "host": "hs2.internal",
        "port": 10001,
        "database": "sales",
        "user": "etl",
        "password": "p@ss",
        "auth_mechanism": "LDAP",
    }
    assert parse_hive_dsn("hive://hs2/?auth=GSSAPI&kerberos_service_name=hive&use_ssl=true") == {
        "host": "hs2",
        "port": 10000,
        "database": "default",
        "auth_mechanism": "GSSAPI",
        "kerberos_service_name": "hive",
        "use_ssl": True,
    }
    with pytest.raises(FatalError, match="hive dsn"):
        parse_hive_dsn("mysql://h/db")
    with pytest.raises(FatalError, match="auth"):
        parse_hive_dsn("hive://h/db?auth=MAGIC")


# --- Redshift COPY into the target ---------------------------------------------


def test_replace_mode_copies_then_swaps_the_unit_range_in_one_transaction() -> None:
    spec = make_spec(REDSHIFT)
    [unit] = plan_units(spec)
    con = RecordingConnection(answer=counts(copy=7))
    rows = RedshiftCopyLoader(spec.target, connect=lambda: con).load(unit, empty=False)

    stage = f'"arsenal_stage_{unit.unit.unit_id}"'
    assert rows == 7
    assert con.log == [
        f'CREATE TEMP TABLE {stage} (LIKE "analytics"."orders")',
        f"COPY {stage} FROM '{unit.uri}' IAM_ROLE '{COPY_ROLE}' FORMAT AS PARQUET",
        "SELECT pg_last_copy_count()",
        'DELETE FROM "analytics"."orders" WHERE ' + DAY_RANGE,
        f'INSERT INTO "analytics"."orders" SELECT * FROM {stage}',
        f"DROP TABLE {stage}",
    ]
    assert con.committed == 1 and con.rolled_back == 0


def test_replace_without_split_replaces_the_whole_table() -> None:
    spec = TransferSpec.model_validate(make_spec(REDSHIFT).model_dump() | {"split": None})
    [unit] = plan_units(spec)
    con = RecordingConnection(answer=counts())
    RedshiftCopyLoader(spec.target, connect=lambda: con).load(unit, empty=False)
    assert 'DELETE FROM "analytics"."orders"' in con.log


def test_empty_export_in_replace_mode_only_clears_the_range() -> None:
    spec = make_spec(REDSHIFT)
    [unit] = plan_units(spec)
    con = RecordingConnection()
    assert RedshiftCopyLoader(spec.target, connect=lambda: con).load(unit, empty=True) == 0
    assert con.log == ['DELETE FROM "analytics"."orders" WHERE ' + DAY_RANGE]
    assert con.committed == 1


def test_merge_mode_deletes_matching_keys() -> None:
    spec = make_spec(REDSHIFT, mode="merge", merge_key=["id", "d"])
    [unit] = plan_units(spec)
    con = RecordingConnection(answer=counts())
    RedshiftCopyLoader(spec.target, connect=lambda: con).load(unit, empty=False)
    stage = f'"arsenal_stage_{unit.unit.unit_id}"'
    assert (
        f'DELETE FROM "analytics"."orders" USING {stage} WHERE '
        f'"analytics"."orders"."id" = {stage}."id" AND "analytics"."orders"."d" = {stage}."d"'
    ) in con.log


def test_empty_export_in_merge_mode_touches_nothing() -> None:
    spec = make_spec(REDSHIFT, mode="merge", merge_key=["id"])
    [unit] = plan_units(spec)

    def refuse() -> RecordingConnection:
        raise AssertionError("merge of nothing must not connect")

    assert RedshiftCopyLoader(spec.target, connect=refuse).load(unit, empty=True) == 0


def test_copy_failure_rolls_back_and_hides_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = make_spec(REDSHIFT)
    [unit] = plan_units(spec)
    con = RecordingConnection(
        fail_on="COPY", error=RuntimeError("Spectrum error for user etl password s3cr3t-pw")
    )
    loader = RedshiftCopyLoader(spec.target, connect=lambda: con, secrets=["s3cr3t-pw"])
    with pytest.raises(FatalError) as caught:
        loader.load(unit, empty=False)
    assert "s3cr3t-pw" not in str(caught.value)
    assert con.rolled_back == 1 and con.committed == 0 and con.closed == 1


def test_a_failed_connect_never_shows_the_dsn_password(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "very-secret-pw"
    monkeypatch.setenv("DW", f"redshift://loader:{password}@dw:5439/dw")

    class FakeDriver:
        @staticmethod
        def connect(**arguments: Any) -> None:
            raise OperationalError(f"auth failed for {arguments['user']} / {password}")

    monkeypatch.setitem(sys.modules, "redshift_connector", FakeDriver)
    spec = make_spec(REDSHIFT)
    [unit] = plan_units(spec)
    with pytest.raises(RetryableError) as caught:
        RedshiftCopyLoader(spec.target).load(unit, empty=False)
    assert password not in str(caught.value) and "***" in str(caught.value)
