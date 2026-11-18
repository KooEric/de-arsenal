"""`pugio ddl`: Trino Hive external table DDL from the recorded schema.

No Trino is available to these tests; the statement text is what is checked.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from arsenal_core.errors import FatalError
from pugio.cli import app
from pugio.ddl import trino_hive_external_table, trino_type

runner = CliRunner()


def test_statement_from_a_schema_snapshot() -> None:
    schema = [
        {"name": "order_id", "type": "int64", "nullable": True},
        {"name": "zip", "type": "string", "nullable": True},
        {"name": "amount", "type": "double", "nullable": True},
        {"name": "ordered on", "type": "date32[day]", "nullable": True},
        {"name": "created_at", "type": "timestamp[s]", "nullable": True},
        {"name": "seen_at", "type": "timestamp[us, tz=UTC]", "nullable": True},
        {"name": "price", "type": "decimal128(10, 2)", "nullable": True},
        {"name": "memo", "type": "null", "nullable": True},
    ]

    statement = trino_hive_external_table(
        schema_json=json.dumps(schema),
        location="s3://bucket/lake/orders",
        catalog="hive",
        schema="analytics",
        table="orders",
    )

    assert statement == (
        'CREATE TABLE IF NOT EXISTS "hive"."analytics"."orders" (\n'
        '    "order_id" BIGINT,\n'
        '    "zip" VARCHAR,\n'
        '    "amount" DOUBLE,\n'
        '    "ordered on" DATE,\n'
        '    "created_at" TIMESTAMP(6),\n'
        '    "seen_at" TIMESTAMP(6) WITH TIME ZONE,\n'
        '    "price" DECIMAL(10, 2),\n'
        '    "memo" VARCHAR\n'
        ")\nWITH (\n"
        "    external_location = 's3://bucket/lake/orders/',\n"
        "    format = 'PARQUET'\n);"
    )


def test_unknown_types_and_quotes() -> None:
    with pytest.raises(FatalError, match="no Trino type for Arrow type 'list<item: int64>'"):
        trino_type("list<item: int64>", "tags")
    statement = trino_hive_external_table(
        schema_json='[{"name": "a\\"b", "type": "bool"}]',
        location="s3://b/it's",
        catalog="hive",
        schema="s",
        table="t",
    )
    assert '"a""b" BOOLEAN' in statement and "'s3://b/it''s/'" in statement


def _pipeline(tmp_path: Path, sink: Mapping[str, object]) -> Path:
    (tmp_path / "in").mkdir(exist_ok=True)
    (tmp_path / "in" / "a.csv").write_text("id,zip,day\n1,02139,2026-11-01\n", "utf-8")
    spec = {
        "name": "daily-orders",
        "state_dir": str(tmp_path / ".arsenal"),
        "source": {"type": "file", "path": str(tmp_path / "in" / "*.csv")},
        "sink": dict(sink),
    }
    path = tmp_path / "collect.yaml"
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return path


def test_cli_prints_ddl_after_a_run(tmp_path: Path) -> None:
    out = tmp_path / "lake" / "orders"
    spec_path = _pipeline(tmp_path, {"type": "parquet", "path": str(out)})

    before = runner.invoke(app, ["ddl", str(spec_path)])
    assert before.exit_code == 1 and "run the pipeline once first" in before.output

    assert runner.invoke(app, ["run", str(spec_path)]).exit_code == 0
    result = runner.invoke(app, ["ddl", str(spec_path), "--schema", "analytics"])

    assert result.exit_code == 0, result.output
    assert 'CREATE TABLE IF NOT EXISTS "hive"."analytics"."daily_orders" (' in result.output
    assert '"id" BIGINT' in result.output
    assert '"zip" VARCHAR' in result.output  # leading zeros kept as text
    assert '"day" DATE' in result.output
    assert "format = 'PARQUET'" in result.output


def test_cli_refuses_other_sinks(tmp_path: Path) -> None:
    sink = {"type": "duckdb", "path": str(tmp_path / "o.duckdb"), "table": "t", "merge_key": ["id"]}
    result = runner.invoke(app, ["ddl", str(_pipeline(tmp_path, sink))])
    assert result.exit_code == 1 and "describes a parquet sink" in result.output
