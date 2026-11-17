from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from ballista.artifacts import ArtifactRepository, InMemoryBlobStore, RunManifest
from ballista.iceberg import (
    MANIFEST_CREATED_PROPERTY,
    MANIFEST_HASH_PROPERTY,
    RUN_ID_PROPERTY,
    IcebergOutputCommitter,
    SnapshotInputReader,
    StaleRunError,
    table_identifier,
)
from ballista.worker import execute_manifest

from arsenal_core.errors import FatalError
from arsenal_core.execution import ArtifactRef


def _samples() -> Any:
    path = Path(__file__).with_name("test_artifacts.py")
    spec = importlib.util.spec_from_file_location("ballista_tests_iceberg_samples", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(**context_overrides: Any) -> RunManifest:
    value: RunManifest = _samples().manifest()
    if context_overrides:
        context = replace(value.request.context, **context_overrides)
        value = replace(value, request=replace(value.request, context=context))
    return value


# --- fakes standing in for a SparkSession with an Iceberg catalog -------------


class FakeTable:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []
        self.rows: list[tuple[Any, ...]] = []


class FakeSpark:
    def __init__(self) -> None:
        self.tables: dict[str, FakeTable] = {}
        self.clock = datetime(2026, 8, 22, 12, tzinfo=UTC)
        self.next_snapshot = 1000
        self.catalog = self
        self.read = FakeReader(self)

    def tableExists(self, table: str) -> bool:  # noqa: N802 - Spark API name
        return table in self.tables

    def sql(self, query: str) -> Any:
        table = query.split(" FROM ")[1].split(".snapshots")[0]
        snapshots = sorted(
            self.tables[table].snapshots, key=lambda row: row["committed_at"], reverse=True
        )
        return _Collected(snapshots)

    def commit(self, table: str, rows: list[tuple[Any, ...]], properties: dict[str, str]) -> None:
        target = self.tables.setdefault(table, FakeTable())
        target.rows = list(rows)
        self.next_snapshot += 1
        self.clock += timedelta(seconds=1)
        target.snapshots.append(
            {
                "snapshot_id": self.next_snapshot,
                "committed_at": self.clock,
                "summary": {**properties, "total-records": str(len(rows))},
            }
        )


class _Collected:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def collect(self) -> list[dict[str, Any]]:
        return self._rows


class FakeReader:
    def __init__(self, spark: FakeSpark) -> None:
        self.spark = spark
        self.calls: list[tuple[str, Any]] = []

    def option(self, key: str, value: Any) -> FakeReader:
        self.calls.append((key, value))
        return self

    def table(self, name: str) -> str:
        self.calls.append(("table", name))
        return f"frame:{name}"

    def parquet(self, path: str) -> str:
        self.calls.append(("parquet", path))
        return f"frame:{path}"


class FakeSchema:
    def jsonValue(self) -> dict[str, Any]:  # noqa: N802 - Spark API name
        return {"type": "struct", "fields": [{"name": "gross", "type": "double"}]}


class FakeFrame:
    schema = FakeSchema()

    def __init__(self, spark: FakeSpark, rows: list[tuple[Any, ...]]) -> None:
        self.spark = spark
        self.rows = rows
        self.writes = 0

    def writeTo(self, table: str) -> FakeWriter:  # noqa: N802 - Spark API name
        self.writes += 1
        return FakeWriter(self, table)

    def limit(self, count: int) -> FakeFrame:
        return FakeFrame(self.spark, self.rows[:count])


class FakeWriter:
    def __init__(self, frame: FakeFrame, table: str) -> None:
        self.frame = frame
        self.table = table
        self.properties: dict[str, str] = {}

    def option(self, key: str, value: str) -> FakeWriter:
        assert key.startswith("snapshot-property.")
        self.properties[key.removeprefix("snapshot-property.")] = value
        return self

    def using(self, provider: str) -> FakeWriter:
        assert provider == "iceberg"
        return self

    def create(self) -> None:
        if self.table in self.frame.spark.tables:
            raise RuntimeError("TableAlreadyExistsException")
        self.frame.spark.commit(self.table, self.frame.rows, self.properties)

    def overwrite(self, condition: Any) -> None:
        assert condition == ("lit", True)
        self.frame.spark.commit(self.table, self.frame.rows, self.properties)


class FakeFunctions:
    @staticmethod
    def lit(value: Any) -> tuple[str, Any]:
        return ("lit", value)


def committer(spark: FakeSpark) -> IcebergOutputCommitter:
    return IcebergOutputCommitter(spark, FakeFunctions(), clock=lambda: spark.clock)


TABLE = "arsenal.analytics.daily"


# --- tests ----------------------------------------------------------------------


def test_first_commit_creates_table_and_tags_snapshot_with_run_identity() -> None:
    spark = FakeSpark()
    value = manifest()

    proof = committer(spark).commit(FakeFrame(spark, [(1.0,), (2.0,)]), value)

    snapshots = spark.tables[TABLE].snapshots
    assert len(snapshots) == 2  # empty create, then the tagged overwrite
    assert RUN_ID_PROPERTY not in snapshots[0]["summary"]
    summary = snapshots[1]["summary"]
    assert summary[RUN_ID_PROPERTY] == "run-1"
    assert summary[MANIFEST_HASH_PROPERTY] == value.content_hash
    assert summary[MANIFEST_CREATED_PROPERTY] == "2026-08-22T00:00:00+00:00"
    assert proof.output_version == proof.output.version == "1002"
    assert proof.row_count == 2
    assert proof.input_versions == ("snapshot-1",)


def test_proof_is_accepted_by_artifact_repository() -> None:
    spark = FakeSpark()
    value = manifest()
    repository = ArtifactRepository("s3://lake/staging", InMemoryBlobStore())
    repository.write_manifest(value)

    proof = committer(spark).commit(FakeFrame(spark, [(1.0,)]), value)

    repository.write_output_proof(value, proof)
    assert repository.read_output_proof(value) is not None


def test_retry_of_same_run_returns_existing_snapshot_without_writing() -> None:
    spark = FakeSpark()
    value = manifest()
    first = committer(spark).commit(FakeFrame(spark, [(1.0,)]), value)

    retry_frame = FakeFrame(spark, [(1.0,)])
    second = committer(spark).commit(retry_frame, value)

    assert retry_frame.writes == 0
    assert len(spark.tables[TABLE].snapshots) == 2
    assert second.output_version == first.output_version


def test_next_run_overwrites_previous_result_in_one_snapshot() -> None:
    spark = FakeSpark()
    committer(spark).commit(FakeFrame(spark, [(1.0,), (2.0,)]), manifest())
    later = manifest(run_id="run-2")
    later = replace(later, created_at=later.created_at + timedelta(hours=1))

    proof = committer(spark).commit(FakeFrame(spark, [(3.0,)]), later)

    assert spark.tables[TABLE].rows == [(3.0,)]
    assert proof.row_count == 1
    assert len(spark.tables[TABLE].snapshots) == 3


def test_stale_attempt_never_overwrites_a_newer_run() -> None:
    spark = FakeSpark()
    newer = manifest(run_id="run-2")
    newer = replace(newer, created_at=newer.created_at + timedelta(hours=1))
    committer(spark).commit(FakeFrame(spark, [(9.0,)]), newer)

    with pytest.raises(StaleRunError):
        committer(spark).commit(FakeFrame(spark, [(1.0,)]), manifest())
    assert spark.tables[TABLE].rows == [(9.0,)]


def test_retry_attempt_of_same_run_reuses_committed_snapshot() -> None:
    spark = FakeSpark()
    first = committer(spark).commit(FakeFrame(spark, [(1.0,)]), manifest())
    retry = replace(manifest(), attempt=1)

    frame = FakeFrame(spark, [(1.0,)])
    second = committer(spark).commit(frame, retry)

    assert frame.writes == 0
    assert second.output_version == first.output_version


def test_same_run_id_with_different_request_is_rejected() -> None:
    spark = FakeSpark()
    committer(spark).commit(FakeFrame(spark, [(1.0,)]), manifest())
    tampered = manifest(revision="revision-changed")

    with pytest.raises(FatalError, match="different request"):
        committer(spark).commit(FakeFrame(spark, [(1.0,)]), tampered)


def test_execute_manifest_accepts_the_iceberg_committer(monkeypatch: pytest.MonkeyPatch) -> None:
    spark = FakeSpark()
    value = manifest()

    class Reader:
        def read(self, spark: Any, inputs: Sequence[ArtifactRef]) -> FakeFrame:
            return FakeFrame(spark, [(2.0,)])

    class Functions:
        def expr(self, expression: str) -> str:
            return expression

        def col(self, column: str) -> str:
            return column

    def passthrough_plan(frame: Any, *_args: Any) -> Any:
        return frame

    import ballista.worker as worker

    monkeypatch.setattr(worker, "apply_spark_plan", passthrough_plan)
    proof = execute_manifest(
        value, spark=spark, functions=Functions(), reader=Reader(), committer=committer(spark)
    )
    assert proof.output_version is not None


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("iceberg://analytics.daily", "arsenal.analytics.daily"),
        ("iceberg://analytics/daily", "arsenal.analytics.daily"),
    ],
)
def test_table_identifier_maps_uri_to_spark_catalog(uri: str, expected: str) -> None:
    assert table_identifier(uri) == expected


@pytest.mark.parametrize(
    "uri",
    [
        "iceberg://daily",
        "iceberg://analytics.daily;DROP",
        "iceberg://analytics.daily?x=1",
        "s3://lake/daily",
        "iceberg://analytics.`daily`",
    ],
)
def test_table_identifier_rejects_unsafe_or_incomplete_names(uri: str) -> None:
    with pytest.raises(FatalError):
        table_identifier(uri)


def test_reader_reads_iceberg_inputs_at_recorded_snapshot() -> None:
    spark = FakeSpark()

    frame = SnapshotInputReader().read(
        spark, (ArtifactRef("iceberg://raw.orders", "iceberg", version="42"),)
    )

    assert frame == "frame:arsenal.raw.orders"
    assert ("snapshot-id", 42) in spark.read.calls


def test_reader_refuses_parquet_input_that_changed_since_planning() -> None:
    spark = FakeSpark()
    reader = SnapshotInputReader(fingerprint=lambda uri: "listing:new")

    with pytest.raises(FatalError, match="changed since"):
        reader.read(spark, (ArtifactRef("s3://lake/in", "parquet", version="listing:old"),))


def test_reader_reads_unchanged_parquet_through_s3a() -> None:
    spark = FakeSpark()
    reader = SnapshotInputReader(fingerprint=lambda uri: "listing:same")

    frame = reader.read(spark, (ArtifactRef("s3://lake/in", "parquet", version="listing:same"),))

    assert frame == "frame:s3a://lake/in"


@pytest.mark.parametrize(
    "inputs",
    [
        (),
        (ArtifactRef("iceberg://a.b", "iceberg", version="1"),) * 2,
        (ArtifactRef("iceberg://a.b", "iceberg"),),
    ],
)
def test_reader_requires_exactly_one_versioned_input(inputs: tuple[ArtifactRef, ...]) -> None:
    with pytest.raises(FatalError):
        SnapshotInputReader().read(FakeSpark(), inputs)


def test_losing_a_create_race_still_commits_the_tagged_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spark = FakeSpark()
    real_exists = spark.tableExists
    checks: list[bool] = []

    def racing_exists(table: str) -> bool:
        # The pre-create check says "absent" while another attempt creates it.
        if len(checks) == 2:
            spark.commit(table, [], {})
            checks.append(False)
            return False
        checks.append(real_exists(table))
        return checks[-1]

    monkeypatch.setattr(spark, "tableExists", racing_exists)
    proof = committer(spark).commit(FakeFrame(spark, [(1.0,)]), manifest())

    assert checks[:3] == [False, False, False]  # create raised, then re-checked
    assert proof.row_count == 1
    assert spark.tables[TABLE].rows == [(1.0,)]
