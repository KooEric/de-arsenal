"""Column check: the query's names and types against the target table, before any export."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from _transfer_fakes import RecordingConnection

from arsenal_core.errors import FatalError
from pugio.transfer.columns import (
    Column,
    compare_columns,
    from_hive,
    from_redshift_catalog,
    from_redshift_oid,
)
from pugio.transfer.exporters import HiveExporter, RedshiftUnloadExporter
from pugio.transfer.loader import RedshiftCopyLoader
from pugio.transfer.spec import HiveQuerySource, RedshiftQuerySource, TransferSpec


def named(*names: str) -> list[Column]:
    """Columns whose type does not matter to the test (same family on both sides)."""
    return [Column(name, "varchar", "string") for name in names]


def make_spec(source: dict[str, Any], table: str = "analytics.orders") -> TransferSpec:
    return TransferSpec.model_validate(
        {
            "name": "t",
            "source": source,
            "staging_path": "s3://bucket/stage",
            "target": {"dsn_env": "DW", "table": table, "iam_role_env": "R"},
        }
    )


REDSHIFT = {
    "engine": "redshift",
    "dsn_env": "SRC",
    "query": "SELECT id, city FROM t",
    "iam_role_env": "ROLE",
}
HIVE = {
    "engine": "hive",
    "dsn_env": "H",
    "query": "SELECT id, city FROM t",
    "settings": {"tez.queue.name": "etl"},
}


# --- names ----------------------------------------------------------------------


def test_same_names_in_the_same_order_pass() -> None:
    compare_columns(named("id", "City"), named("id", "city"), table="s.t")  # case-insensitive


def test_swapped_columns_are_reported_by_position() -> None:
    with pytest.raises(FatalError) as caught:
        compare_columns(named("id", "country", "city"), named("id", "city", "country"), table="s.t")
    message = str(caught.value)
    assert "by position" in message
    assert "position 2: query 'country', table 'city'" in message
    assert "position 3: query 'city', table 'country'" in message


def test_a_missing_or_extra_column_is_reported() -> None:
    with pytest.raises(FatalError, match="position 3: query \\(none\\), table 'amount'"):
        compare_columns(named("id", "city"), named("id", "city", "amount"), table="s.t")
    with pytest.raises(FatalError, match="position 3: query 'amount', table \\(none\\)"):
        compare_columns(named("id", "city", "amount"), named("id", "city"), table="s.t")


def test_an_unaliased_expression_gets_a_hint() -> None:
    with pytest.raises(FatalError, match="alias"):
        compare_columns(named("id", "_c1"), named("id", "total"), table="s.t")


def test_a_missing_target_table_is_reported() -> None:
    with pytest.raises(FatalError, match="not found"):
        compare_columns(named("id"), [], table="s.t")


def test_long_mismatch_lists_are_cut_short() -> None:
    query = named(*(f"q{i}" for i in range(30)))
    table = named(*(f"t{i}" for i in range(30)))
    with pytest.raises(FatalError) as caught:
        compare_columns(query, table, table="s.t")
    assert "position 10:" in str(caught.value)
    assert "position 11:" not in str(caught.value)
    assert "20 more" in str(caught.value)


# --- types ----------------------------------------------------------------------


def test_type_families_must_match() -> None:
    query = [from_hive("id", "BIGINT"), from_hive("amount", "DOUBLE")]
    table = [
        from_redshift_catalog("id", "bigint", None, 64, 0),
        from_redshift_catalog("amount", "numeric", None, 18, 2),
    ]
    with pytest.raises(FatalError) as caught:
        compare_columns(query, table, table="s.t")
    message = str(caught.value)
    assert "types" in message
    assert "position 2 'amount': query DOUBLE (float), table numeric(18,2) (decimal)" in message
    assert "position 1" not in message


def test_sizes_within_a_family_and_integer_into_decimal_pass() -> None:
    query = [
        from_hive("id", "INT"),
        from_hive("code", "STRING"),
        from_hive("qty", "BIGINT"),
        from_redshift_oid("ratio", 700),
    ]
    table = [
        from_redshift_catalog("id", "bigint", None, 64, 0),
        from_redshift_catalog("code", "character varying", 32, None, None),
        from_redshift_catalog("qty", "numeric", None, 20, 0),
        from_redshift_catalog("ratio", "double precision", None, 53, None),
    ]
    compare_columns(query, table, table="s.t")


def test_timestamp_and_timestamptz_do_not_mix() -> None:
    query = [from_redshift_oid("at", 1184)]
    table = [from_redshift_catalog("at", "timestamp without time zone", None, None, None)]
    with pytest.raises(FatalError, match="timestamptz.*timestamp"):
        compare_columns(query, table, table="s.t")


def test_types_are_not_compared_when_check_types_is_off() -> None:
    query = [from_hive("id", "STRING")]
    table = [from_redshift_catalog("id", "bigint", None, 64, 0)]
    compare_columns(query, table, table="s.t", check_types=False)


def test_unknown_types_are_logged_not_refused(caplog: pytest.LogCaptureFixture) -> None:
    query = [from_hive("tags", "ARRAY<STRING>")]
    table = [from_redshift_catalog("tags", "super", None, None, None)]
    with caplog.at_level(logging.WARNING, logger="pugio.transfer"):
        compare_columns(query, table, table="s.t")
    assert "tags" in caplog.text and "not checked" in caplog.text


@pytest.mark.parametrize(
    ("type_code", "family"),
    [
        ("TINYINT", "integer"),
        ("INT_TYPE", "integer"),
        ("DECIMAL(10,2)", "decimal"),
        ("VARCHAR", "string"),
        ("CHAR", "string"),
        ("FLOAT", "float"),
        ("BOOLEAN", "boolean"),
        ("DATE", "date"),
        ("TIMESTAMP", "timestamp"),
        ("TIMESTAMP WITH LOCAL TIME ZONE", "timestamptz"),
        ("BINARY", None),
        ("MAP<STRING,INT>", None),
    ],
)
def test_hive_type_families(type_code: str, family: str | None) -> None:
    assert from_hive("c", type_code).family == family


@pytest.mark.parametrize(
    ("oid", "family"),
    [
        (21, "integer"),
        (23, "integer"),
        (20, "integer"),
        (701, "float"),
        (1700, "decimal"),
        (1043, "string"),
        (1042, "string"),
        (25, "string"),
        (16, "boolean"),
        (1082, "date"),
        (1114, "timestamp"),
        (1184, "timestamptz"),
        (1083, None),
        (4000, None),
    ],
)
def test_redshift_oid_families(oid: int, family: str | None) -> None:
    assert from_redshift_oid("c", oid).family == family


def test_catalog_labels_carry_length_and_precision() -> None:
    assert from_redshift_catalog("c", "character varying", 256, None, None).type == (
        "character varying(256)"
    )
    assert from_redshift_catalog("c", "numeric", None, 18, 2).type == "numeric(18,2)"
    assert from_redshift_catalog("c", "integer", None, 32, 0).type == "integer"


# --- reading the columns ----------------------------------------------------------


def test_redshift_query_columns_come_from_a_limit_0_query() -> None:
    spec = make_spec(REDSHIFT)
    assert isinstance(spec.source, RedshiftQuerySource)
    con = RecordingConnection(columns=[("id", 20), ("city", 1043)])
    columns = RedshiftUnloadExporter(spec.source, connect=lambda: con).columns()
    assert [(c.name, c.family) for c in columns] == [("id", "integer"), ("city", "string")]
    assert con.log == ["SELECT * FROM ( SELECT id, city FROM t ) arsenal_cols LIMIT 0"]


def test_hive_query_columns_drop_the_alias_prefix_and_apply_settings() -> None:
    spec = make_spec(HIVE)
    assert isinstance(spec.source, HiveQuerySource)
    con = RecordingConnection(
        columns=[("arsenal_cols.id", "BIGINT"), ("arsenal_cols.city", "STRING")]
    )
    exporter = HiveExporter(spec.source, connect=lambda: con, list_files=lambda _: 1)
    columns = exporter.columns()
    assert [(c.name, c.type) for c in columns] == [("id", "BIGINT"), ("city", "STRING")]
    assert con.log == [
        "SET tez.queue.name=etl",
        "SELECT * FROM ( SELECT id, city FROM t ) arsenal_cols LIMIT 0",
    ]


@pytest.mark.parametrize(
    ("table", "schema_sql", "params"),
    [
        ("analytics.orders", "%s", ("analytics", "orders")),
        ("orders", "current_schema()", ("orders",)),
        ("dw.analytics.orders", "%s", ("analytics", "orders")),
    ],
)
def test_target_columns_are_read_in_ordinal_order(
    table: str, schema_sql: str, params: tuple[str, ...]
) -> None:
    spec = make_spec(REDSHIFT, table=table)
    rows = [("id", "bigint", None, 64, 0), ("city", "character varying", 64, None, None)]
    con = RecordingConnection(answer=lambda sql: rows)
    columns = RedshiftCopyLoader(spec.target, connect=lambda: con).columns()
    assert [(c.name, c.type, c.family) for c in columns] == [
        ("id", "bigint", "integer"),
        ("city", "character varying(64)", "string"),
    ]
    assert con.log == [
        "SELECT column_name, data_type, character_maximum_length, numeric_precision, "
        "numeric_scale FROM information_schema.columns "
        f"WHERE table_schema = {schema_sql} AND table_name = %s ORDER BY ordinal_position"
    ]
    assert con.params == [params]
