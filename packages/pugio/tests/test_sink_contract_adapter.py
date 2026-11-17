"""Parameterized P2 sink contract tests.

Each backend is exercised through the same capability/proof adapter. The
Postgres parameter is skipped when Docker is unavailable, matching the rest of
Pugio's container-backed integration suite.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import duckdb
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from psycopg import sql

from arsenal_core.errors import FatalError
from arsenal_core.execution import SinkCapabilities
from arsenal_core.spec.models import DuckDBSinkSpec, ParquetSinkSpec, PostgresSinkSpec, SinkSpec
from arsenal_core.state import UnitSpec
from pugio.sinks import (
    DUCKDB_CAPABILITIES,
    LOCAL_PARQUET_CAPABILITIES,
    OBJECT_STORAGE_PARQUET_CAPABILITIES,
    POSTGRES_CAPABILITIES,
    CapableSink,
    DuckDBSink,
    OutputProofContext,
    ParquetSink,
    PostgresSink,
    SinkContractAdapter,
    build_sink,
    build_sink_adapter,
    capabilities_for_spec,
)

Rows = list[dict[str, object]]
ReadBack = Callable[[], Rows]
FailureScope = Callable[[], AbstractContextManager[None]]


@dataclass(frozen=True)
class ContractCase:
    adapter: SinkContractAdapter
    unit: UnitSpec
    readback: ReadBack
    fail_during_publish: FailureScope
    failure_type: type[BaseException]


def _unit(key: str = "batch-1") -> UnitSpec:
    return UnitSpec.create(pipeline="contract-p", source="contract-s", unit_key=key, payload={})


def _batch(value: str, *, include_second_row: bool = False) -> pa.RecordBatch:
    rows = [{"id": 1, "value": value}]
    if include_second_row:
        rows.append({"id": 2, "value": value})
    return pa.RecordBatch.from_pylist(rows)


def _sorted(rows: Rows) -> Rows:
    return sorted(rows, key=lambda row: cast(int, row["id"]))


@pytest.fixture(scope="module")
def contract_pg_url(require_docker: None) -> Iterator[str]:
    pgtc = pytest.importorskip("testcontainers.postgres")
    with pgtc.PostgresContainer("postgres:16-alpine") as container:
        yield container.get_connection_url(driver=None)


@pytest.fixture(params=("parquet", "duckdb", "postgres"), ids=str)
def sink_contract_case(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ContractCase:
    kind = cast(str, request.param)
    unit = _unit()

    if kind == "parquet":
        output_dir = tmp_path / "parquet"
        spec = ParquetSinkSpec(type="parquet", path=str(output_dir))
        sink = ParquetSink(output_dir)

        def readback() -> Rows:
            final = output_dir / f"{unit.unit_id}.parquet"
            if not final.exists():
                return []
            table = pq.read_table(final)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return table.to_pylist()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

        @contextmanager
        def fail_during_publish() -> Generator[None]:
            def fail_replace(*_args: object, **_kwargs: object) -> None:
                raise OSError("simulated atomic replace failure")

            with monkeypatch.context() as patch:
                patch.setattr(os, "replace", fail_replace)
                yield

        return ContractCase(
            adapter=build_sink_adapter(spec, sink=sink),
            unit=unit,
            readback=readback,
            fail_during_publish=fail_during_publish,
            failure_type=OSError,
        )

    if kind == "duckdb":
        db_path = tmp_path / "sink.duckdb"
        spec = DuckDBSinkSpec(
            type="duckdb",
            path=str(db_path),
            table="contract_rows",
            merge_key=["id"],
        )
        sink = DuckDBSink(spec)

        def readback() -> Rows:
            con = duckdb.connect(str(db_path))
            try:
                table = con.execute('SELECT * FROM "contract_rows"').to_arrow_table()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                return table.to_pylist()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            finally:
                con.close()

        @contextmanager
        def fail_during_publish() -> Generator[None]:
            real_connect = duckdb.connect

            class FailAfterDeleteConnection:
                def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
                    self._con = con

                def register(self, name: str, value: object) -> None:
                    self._con.register(name, value)

                def execute(self, statement: str) -> duckdb.DuckDBPyConnection:
                    if statement.lstrip().upper().startswith("INSERT"):
                        raise RuntimeError("simulated failure after DELETE")
                    return self._con.execute(statement)

                def close(self) -> None:
                    self._con.close()

            def failing_connect(path: str) -> FailAfterDeleteConnection:
                return FailAfterDeleteConnection(real_connect(path))

            with monkeypatch.context() as patch:
                patch.setattr(duckdb, "connect", failing_connect)
                yield

        return ContractCase(
            adapter=build_sink_adapter(spec, sink=sink),
            unit=unit,
            readback=readback,
            fail_during_publish=fail_during_publish,
            failure_type=FatalError,
        )

    pg_url = cast(str, request.getfixturevalue("contract_pg_url"))
    table_name = f"contract_{uuid.uuid4().hex}"
    dsn_env = f"PUGIO_CONTRACT_DSN_{uuid.uuid4().hex}"
    monkeypatch.setenv(dsn_env, pg_url)
    spec = PostgresSinkSpec(
        type="postgres",
        dsn_env=dsn_env,
        table=table_name,
        merge_key=["id"],
    )
    sink = PostgresSink(spec)

    def readback() -> Rows:
        with psycopg.connect(pg_url) as con, con.cursor() as cur:
            cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table_name)))
            columns = [description.name for description in cur.description or []]
            return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]

    @contextmanager
    def fail_during_publish() -> Generator[None]:
        def fail_after_delete(con: psycopg.Connection, _batch: pa.RecordBatch) -> None:
            con.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table_name)))
            raise psycopg.ProgrammingError("simulated failure after DELETE")

        with monkeypatch.context() as patch:
            patch.setattr(sink, "_upsert", fail_after_delete)
            yield

    return ContractCase(
        adapter=build_sink_adapter(spec, sink=sink),
        unit=unit,
        readback=readback,
        fail_during_publish=fail_during_publish,
        failure_type=FatalError,
    )


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        pytest.param(
            ParquetSinkSpec(type="parquet", path="local-output"),
            LOCAL_PARQUET_CAPABILITIES,
            id="local-parquet",
        ),
        pytest.param(
            ParquetSinkSpec(type="parquet", path="s3://bucket/output"),
            OBJECT_STORAGE_PARQUET_CAPABILITIES,
            id="object-parquet",
        ),
        pytest.param(
            DuckDBSinkSpec(type="duckdb", path="sink.duckdb", table="t", merge_key=["id"]),
            DUCKDB_CAPABILITIES,
            id="duckdb",
        ),
        pytest.param(
            PostgresSinkSpec(type="postgres", dsn_env="PG_DSN", table="t", merge_key=["id"]),
            POSTGRES_CAPABILITIES,
            id="postgres",
        ),
    ],
)
def test_capability_declaration_matches_built_sink(
    spec: SinkSpec, expected: SinkCapabilities
) -> None:
    sink = cast(CapableSink, build_sink(spec))
    assert capabilities_for_spec(spec) == expected
    assert sink.capabilities == expected


def test_postgres_output_proof_uses_table_identity_not_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSink:
        def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
            assert unit.unit_id
            assert batch.num_rows == 1

    monkeypatch.setenv("SECRET_PG_DSN", "postgresql://user:secret@database/private")
    spec = PostgresSinkSpec(
        type="postgres",
        dsn_env="SECRET_PG_DSN",
        table="safe_table",
        merge_key=["id"],
    )
    proof = build_sink_adapter(spec, sink=RecordingSink()).write_with_proof(
        _unit(),
        _batch("value"),
        context=OutputProofContext(run_id="run", revision="revision", plan_hash="plan"),
    )

    assert proof.output.uri == "postgres:///safe_table"
    assert "secret" not in proof.output.uri


def test_write_twice_returns_proofs_and_one_visible_result(
    sink_contract_case: ContractCase,
) -> None:
    context = OutputProofContext(
        run_id="run-1",
        revision="revision-1",
        plan_hash="plan-1",
        input_versions=("input-1",),
    )
    first = sink_contract_case.adapter.write_with_proof(
        sink_contract_case.unit,
        _batch("same", include_second_row=True),
        context=context,
    )
    second = sink_contract_case.adapter.write_with_proof(
        sink_contract_case.unit,
        _batch("same", include_second_row=True),
        context=context,
    )

    assert sink_contract_case.adapter.capabilities.idempotent_batch_write is True
    assert _sorted(sink_contract_case.readback()) == [
        {"id": 1, "value": "same"},
        {"id": 2, "value": "same"},
    ]
    assert first.output == second.output
    assert first.output.version == sink_contract_case.unit.unit_id
    assert first.output_version == sink_contract_case.unit.unit_id
    assert first.row_count == second.row_count == 2
    assert first.schema_hash == second.schema_hash
    assert first.input_versions == ("input-1",)


def test_partial_failure_leaves_previous_visible_result(
    sink_contract_case: ContractCase,
) -> None:
    context = OutputProofContext(run_id="run-2", revision="revision-2", plan_hash="plan-2")
    sink_contract_case.adapter.write_with_proof(
        sink_contract_case.unit,
        _batch("before"),
        context=context,
    )
    before = sink_contract_case.readback()

    with (
        sink_contract_case.fail_during_publish(),
        pytest.raises(sink_contract_case.failure_type),
    ):
        sink_contract_case.adapter.write_with_proof(
            sink_contract_case.unit,
            _batch("after", include_second_row=True),
            context=context,
        )

    assert sink_contract_case.adapter.capabilities.atomic_publish is True
    assert sink_contract_case.readback() == before
