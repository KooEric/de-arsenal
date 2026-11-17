"""DuckDB ↔ Spark equivalence over the portable golden corpus.

DuckDB cases always run. Spark cases need ``pyspark`` and a Java runtime and
are skipped otherwise; CI's ``portable-equivalence`` job provides both.
"""

from __future__ import annotations

import importlib.util
import math
import os
import shutil
from collections.abc import Iterator, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from ballista.plan import plan_to_mapping
from ballista.worker import apply_spark_plan

from gladius.compile import compile_sql
from gladius.plan import preflight_portable
from gladius.spec import TransformSpec


def _corpus() -> Any:
    path = Path(__file__).with_name("portable_corpus.py")
    spec = importlib.util.spec_from_file_location("ballista_portable_corpus", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORPUS = _corpus()
CASE_IDS = [case["id"] for case in CORPUS.CASES]


@pytest.fixture(scope="module")
def input_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("portable") / "input"
    directory.mkdir()
    schema = pa.schema(
        [
            ("id", pa.int64()),
            ("name", pa.string()),
            ("amount", pa.float64()),
            ("qty", pa.int64()),
            ("day", pa.date32()),
        ]
    )
    table = pa.Table.from_pylist(CORPUS.INPUT_ROWS, schema=schema)  # pyright: ignore[reportUnknownMemberType]
    pq.write_table(table, directory / "part-0.parquet")  # pyright: ignore[reportUnknownMemberType]
    return directory


def _spec(case: dict[str, Any], input_uri: str) -> TransformSpec:
    body: dict[str, Any] = {
        "name": case["id"],
        "input": input_uri,
        "steps": case.get("steps", []),
        "output": "iceberg://corpus.out",
    }
    if "map" in case:
        body["map"] = case["map"]
    return TransformSpec.model_validate(body)


def _normalize(rows: Sequence[Sequence[Any]]) -> list[tuple[Any, ...]]:
    def cell(value: Any) -> Any:
        if isinstance(value, float):
            return round(value, 9) if math.isfinite(value) else value
        if isinstance(value, Decimal):
            return value.normalize()
        return value

    normalized = [tuple(cell(value) for value in row) for row in rows]
    return sorted(normalized, key=lambda row: [(v is None, str(v)) for v in row])


def test_every_case_is_portable() -> None:
    for case in CORPUS.CASES:
        preflight_portable(_spec(case, "s3://lake/in"))


@pytest.mark.parametrize("case", CORPUS.CASES, ids=CASE_IDS)
def test_duckdb_renderer_matches_golden(case: dict[str, Any], input_path: Path) -> None:
    spec = _spec(case, str(input_path))
    sql = compile_sql(spec, [input_path / "part-0.parquet"])
    connection = duckdb.connect()
    try:
        cursor = connection.execute(sql)
        columns = [description[0] for description in cursor.description or []]
        rows = cursor.fetchall()
    finally:
        connection.close()

    assert columns == case["columns"]
    assert _normalize(rows) == _normalize(case["expected"])


def _spark_available() -> bool:
    return importlib.util.find_spec("pyspark") is not None and shutil.which("java") is not None


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    if not _spark_available():
        message = "pyspark and a Java runtime are required for Spark equivalence"
        # CI's equivalence job sets this so a broken setup fails instead of skipping.
        if os.environ.get("BALLISTA_REQUIRE_SPARK") == "1":
            pytest.fail(message)
        pytest.skip(message)
    pyspark_sql: Any = importlib.import_module("pyspark.sql")
    session = (
        pyspark_sql.SparkSession.builder.master("local[1]")
        .appName("ballista-portable-corpus")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


@pytest.mark.spark
@pytest.mark.parametrize("case", CORPUS.CASES, ids=CASE_IDS)
def test_spark_renderer_matches_golden(case: dict[str, Any], input_path: Path, spark: Any) -> None:
    functions: Any = importlib.import_module("pyspark.sql.functions")
    plan = plan_to_mapping(preflight_portable(_spec(case, str(input_path))))
    frame = spark.read.parquet(str(input_path))

    result = apply_spark_plan(frame, plan, functions)

    assert list(result.columns) == case["columns"]
    assert _normalize([tuple(row) for row in result.collect()]) == _normalize(case["expected"])
