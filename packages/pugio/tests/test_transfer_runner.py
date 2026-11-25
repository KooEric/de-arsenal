"""`run_transfer`: export → load per unit, resume, export reuse, empty units, failures."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.state import StateStore
from pugio.transfer.columns import Column
from pugio.transfer.exporters import ExportResult
from pugio.transfer.runner import run_transfer, transfer_status
from pugio.transfer.spec import TransferSpec
from pugio.transfer.units import TransferUnit


def named(*names: str) -> list[Column]:
    return [Column(name, "varchar", "string") for name in names]


def make_spec(
    tmp_path: Path,
    query: str = "SELECT * FROM t",
    check_columns: bool = True,
    check_types: bool = True,
) -> TransferSpec:
    return TransferSpec.model_validate(
        {
            "name": "t",
            "check_columns": check_columns,
            "check_types": check_types and check_columns,
            "state_dir": str(tmp_path / "state"),
            "max_attempts": 2,
            "source": {"engine": "hive", "dsn_env": "H", "query": query},
            "split": {"column": "id", "start": 0, "end": 30, "every": 10},
            "staging_path": "s3://bucket/stage",
            "target": {"dsn_env": "D", "table": "s.t", "iam_role_env": "R"},
        }
    )


@dataclass
class FakeExporter:
    rows: int = 5
    fail_on: set[int] = field(default_factory=lambda: set[int]())
    error: Exception = field(default_factory=lambda: FatalError("boom"))
    calls: list[str] = field(default_factory=lambda: [])
    query_columns: list[Column] = field(default_factory=lambda: named("id", "v"))
    column_checks: int = 0

    def columns(self) -> list[Column]:
        self.column_checks += 1
        return self.query_columns

    def export(self, unit: TransferUnit) -> ExportResult:
        self.calls.append(unit.unit.unit_key)
        if unit.lo in self.fail_on:
            raise self.error
        empty = unit.lo == 20
        return ExportResult(rows=0 if empty else self.rows, empty=empty)


@dataclass
class FakeLoader:
    fail_on: set[int] = field(default_factory=lambda: set[int]())
    calls: list[tuple[str, bool]] = field(default_factory=lambda: [])
    table_columns: list[Column] = field(default_factory=lambda: named("id", "v"))

    def columns(self) -> list[Column]:
        return self.table_columns

    def load(self, unit: TransferUnit, *, empty: bool) -> int:
        self.calls.append((unit.unit.unit_key, empty))
        if unit.lo in self.fail_on:
            raise FatalError("copy failed")
        return 0 if empty else 5


def test_every_unit_is_exported_then_loaded(tmp_path: Path) -> None:
    exporter, loader = FakeExporter(), FakeLoader()
    report = run_transfer(make_spec(tmp_path), exporter=exporter, loader=loader)

    assert report.loaded == 3 and report.skipped == 0 and report.rows == 10
    assert report.exported == 3 and report.reused_exports == 0
    assert loader.calls == [("id=0..10", False), ("id=10..20", False), ("id=20..30", True)]


def test_rerun_skips_done_units(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    run_transfer(spec, exporter=FakeExporter(), loader=FakeLoader())
    exporter = FakeExporter()
    report = run_transfer(spec, exporter=exporter, loader=FakeLoader())
    assert report.skipped == 3 and report.loaded == 0 and exporter.calls == []


def test_failed_load_resumes_without_running_the_export_again(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    with pytest.raises(FatalError, match="copy failed"):
        run_transfer(spec, exporter=FakeExporter(), loader=FakeLoader(fail_on={10}))

    status = transfer_status(spec)
    assert status.counts == {"done": 1, "failed": 1, "pending": 1}  # remaining units show
    assert [(f.unit_key, f.error) for f in status.failed] == [("id=10..20", "copy failed")]

    exporter, loader = FakeExporter(), FakeLoader()
    report = run_transfer(spec, exporter=exporter, loader=loader)
    assert exporter.calls == ["id=20..30"]  # the expensive query for id=10..20 is not rerun
    assert report.reused_exports == 1 and report.skipped == 1 and report.loaded == 2
    assert transfer_status(spec).counts == {"done": 3}


def test_reexport_runs_the_query_again_for_a_saved_export(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    with pytest.raises(FatalError):
        run_transfer(spec, exporter=FakeExporter(), loader=FakeLoader(fail_on={10}))
    exporter = FakeExporter()
    report = run_transfer(spec, exporter=exporter, loader=FakeLoader(), reexport=True)
    assert exporter.calls == ["id=10..20", "id=20..30"]
    assert report.reused_exports == 0


def test_failed_export_stops_the_run_and_is_retried_next_time(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    with pytest.raises(FatalError, match="boom"):
        run_transfer(spec, exporter=FakeExporter(fail_on={0}), loader=FakeLoader())
    assert transfer_status(spec).counts == {"failed": 1, "pending": 2}

    exporter = FakeExporter()
    run_transfer(spec, exporter=exporter, loader=FakeLoader())
    assert exporter.calls == ["id=0..10", "id=10..20", "id=20..30"]


def test_temporary_export_errors_are_retried_in_process(tmp_path: Path) -> None:
    attempts: list[int] = []

    @dataclass
    class Flaky(FakeExporter):
        def export(self, unit: TransferUnit) -> ExportResult:
            if unit.lo == 0 and not attempts:
                attempts.append(1)
                raise RetryableError("connection reset")
            return super().export(unit)

    report = run_transfer(
        make_spec(tmp_path), exporter=Flaky(), loader=FakeLoader(), retry_wait=0.0
    )
    assert report.loaded == 3 and attempts == [1]


def test_a_changed_query_gets_fresh_units(tmp_path: Path) -> None:
    run_transfer(make_spec(tmp_path), exporter=FakeExporter(), loader=FakeLoader())
    exporter = FakeExporter()
    run_transfer(make_spec(tmp_path, "SELECT a FROM t"), exporter=exporter, loader=FakeLoader())
    assert len(exporter.calls) == 3


def test_a_second_run_cannot_start_while_one_holds_the_lease(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    store = StateStore(Path(spec.state_dir) / "t.transfer.db")
    try:
        assert store.acquire_lease("transfer:t", "other", 60.0) is not None
        with pytest.raises(FatalError, match="busy"):
            run_transfer(spec, exporter=FakeExporter(), loader=FakeLoader())
    finally:
        store.close()


def test_status_before_any_run(tmp_path: Path) -> None:
    status = transfer_status(make_spec(tmp_path))
    assert status.counts == {} and status.failed == []


def test_column_mismatch_stops_before_any_export(tmp_path: Path) -> None:
    exporter = FakeExporter(query_columns=named("v", "id"))
    with pytest.raises(FatalError, match="by position"):
        run_transfer(make_spec(tmp_path), exporter=exporter, loader=FakeLoader())
    assert exporter.calls == []
    assert transfer_status(make_spec(tmp_path)).counts == {"pending": 3}


def test_columns_are_checked_once_per_run(tmp_path: Path) -> None:
    exporter = FakeExporter()
    run_transfer(make_spec(tmp_path), exporter=exporter, loader=FakeLoader())
    assert exporter.column_checks == 1


def test_no_check_when_every_unit_is_done(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    run_transfer(spec, exporter=FakeExporter(), loader=FakeLoader())
    exporter = FakeExporter(query_columns=named("changed"))
    run_transfer(spec, exporter=exporter, loader=FakeLoader())
    assert exporter.column_checks == 0


def test_check_columns_false_skips_the_check(tmp_path: Path) -> None:
    exporter = FakeExporter(query_columns=named("v", "id"))
    run_transfer(make_spec(tmp_path, check_columns=False), exporter=exporter, loader=FakeLoader())
    assert exporter.column_checks == 0 and len(exporter.calls) == 3


def test_type_mismatch_stops_before_any_export(tmp_path: Path) -> None:
    exporter = FakeExporter(query_columns=[Column("id", "BIGINT", "integer"), *named("v")])
    with pytest.raises(FatalError, match="types"):
        run_transfer(make_spec(tmp_path), exporter=exporter, loader=FakeLoader())
    assert exporter.calls == []


def test_check_types_false_compares_names_only(tmp_path: Path) -> None:
    exporter = FakeExporter(query_columns=[Column("id", "BIGINT", "integer"), *named("v")])
    run_transfer(make_spec(tmp_path, check_types=False), exporter=exporter, loader=FakeLoader())
    assert len(exporter.calls) == 3
