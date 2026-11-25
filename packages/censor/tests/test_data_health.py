import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from _data import DUPLICATE, ROWS, write_duckdb, write_parquet
from _state import snapshot

from arsenal_core.errors import FatalError
from censor import limits
from censor.audit import AuditOutcome, AuditRequest, run_audit
from censor.model import CheckResult, Dimension, Status
from censor.target import DataTarget, DuckDBTarget, FilesTarget, ScanOptions

NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)


def _audit(base: Path, data: DataTarget, **kw: object) -> AuditOutcome:
    request = AuditRequest(root=base, tier="standard", now=NOW, data=data, **kw)  # type: ignore[arg-type]
    return run_audit(request)


def _by_kind(outcome: AuditOutcome) -> dict[str, CheckResult]:
    return {c.id.split(":")[0]: c for c in outcome.report.checks}


def test_clean_parquet_scores_data_health_only(tmp_path: Path) -> None:
    write_parquet(tmp_path / "data" / "a.parquet", ROWS)
    pattern = str(tmp_path / "data" / "*.parquet")
    outcome = _audit(tmp_path, FilesTarget(pattern), key=("id",), time_column="ts")
    checks = _by_kind(outcome)
    assert checks["data_health.rows"].status == Status.PASS
    assert "4 rows" in checks["data_health.rows"].evidence
    assert checks["data_health.key_unique"].status == Status.PASS
    assert checks["data_health.key_not_null"].status == Status.PASS
    assert checks["data_health.period"].status == Status.PASS
    assert "2026-11-01" in checks["data_health.period"].evidence
    assert "2026-11-24" in checks["data_health.period"].evidence
    report = outcome.report
    assert (report.target_kind, report.target, report.tier) == ("files", pattern, "standard")
    scores = {d.dimension: d.score for d in report.dimensions}
    assert scores[Dimension.DATA_HEALTH] is not None
    assert scores[Dimension.CORRECTNESS] is None and scores[Dimension.RELIABILITY] is None
    assert outcome.tool_errors == ()


def test_duplicate_key_and_null_key_fail(tmp_path: Path) -> None:
    rows = [*ROWS, DUPLICATE, {**ROWS[1], "id": None}]
    write_parquet(tmp_path / "a.parquet", rows)
    checks = _by_kind(_audit(tmp_path, FilesTarget(str(tmp_path / "*.parquet")), key=("id",)))
    assert checks["data_health.key_unique"].status == Status.FAIL
    assert "1 values of (id) occur more than once" in checks["data_health.key_unique"].evidence
    assert checks["data_health.key_not_null"].status == Status.FAIL


def test_null_ratio_warns_at_half_and_fails_when_a_column_is_empty(tmp_path: Path) -> None:
    write_parquet(tmp_path / "a.parquet", ROWS)  # note: 3 of 4 NULL
    target = FilesTarget(str(tmp_path / "*.parquet"))
    warned = _by_kind(_audit(tmp_path, target))["data_health.null_ratio"]
    assert warned.status == Status.WARN and "note 75.0%" in warned.evidence
    write_parquet(tmp_path / "a.parquet", [{**r, "note": None, "v": "x"} for r in ROWS])
    failed = _by_kind(_audit(tmp_path, target))["data_health.null_ratio"]
    assert failed.status == Status.FAIL and "note 100.0%" in failed.evidence


def test_without_key_or_time_column_those_checks_are_na(tmp_path: Path) -> None:
    write_parquet(tmp_path / "a.parquet", ROWS)
    checks = _by_kind(_audit(tmp_path, FilesTarget(str(tmp_path / "*.parquet"))))
    assert checks["data_health.key_unique"].status == Status.NA
    assert checks["data_health.period"].status == Status.NA


def test_empty_data_fails_rows_only(tmp_path: Path) -> None:
    write_parquet(tmp_path / "a.parquet", ROWS)
    scan = ScanOptions(since=date(2030, 1, 1))
    outcome = _audit(
        tmp_path, FilesTarget(str(tmp_path / "*.parquet")), time_column="ts", scan=scan
    )
    assert [c.id.split(":")[0] for c in outcome.report.checks if c.dimension == "data_health"] == [
        "data_health.rows"
    ]
    assert _by_kind(outcome)["data_health.rows"].status == Status.FAIL
    assert outcome.report.since == "2030-01-01"


def test_period_in_the_future_is_a_warning(tmp_path: Path) -> None:
    rows = [{**r, "ts": datetime(2027, 6, 1, tzinfo=UTC)} for r in ROWS]
    write_parquet(tmp_path / "a.parquet", rows)
    target = FilesTarget(str(tmp_path / "*.parquet"))
    check = _by_kind(_audit(tmp_path, target, time_column="ts"))["data_health.period"]
    assert check.status == Status.WARN and "future" in check.evidence


def test_csv_and_jsonl_are_profiled_too(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("id,v\n1,a\n1,b\n")
    csv = _by_kind(_audit(tmp_path, FilesTarget(str(tmp_path / "*.csv")), key=("id",)))
    assert csv["data_health.key_unique"].status == Status.FAIL
    (tmp_path / "a.jsonl").write_text('{"id": 1}\n{"id": 2}\n')
    jsonl = _by_kind(_audit(tmp_path, FilesTarget(str(tmp_path / "*.jsonl")), key=("id",)))
    assert jsonl["data_health.key_unique"].status == Status.PASS


def test_bad_targets_are_tool_errors(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="no file matches"):
        _audit(tmp_path, FilesTarget(str(tmp_path / "*.parquet")))
    write_parquet(tmp_path / "a.parquet", ROWS)
    (tmp_path / "b.csv").write_text("id\n1\n")
    with pytest.raises(FatalError, match="cannot read these files together"):
        _audit(tmp_path, FilesTarget(str(tmp_path / "*.*")))
    with pytest.raises(FatalError, match="no column nope"):
        _audit(tmp_path, FilesTarget(str(tmp_path / "*.parquet")), key=("nope",))
    with pytest.raises(FatalError, match="no DuckDB file"):
        _audit(tmp_path, DuckDBTarget(tmp_path / "missing.duckdb", "t"))
    with pytest.raises(FatalError, match="needs --tier standard"):
        run_audit(AuditRequest(root=tmp_path, data=FilesTarget("x")))
    with pytest.raises(FatalError, match="--since needs --time-column"):
        _audit(tmp_path, FilesTarget("x"), scan=ScanOptions(since=date(2026, 1, 1)))
    with pytest.raises(FatalError, match="--sample"):
        _audit(tmp_path, FilesTarget("x"), scan=ScanOptions(sample_percent=0))


def test_duckdb_table_is_profiled_read_only(tmp_path: Path) -> None:
    store = write_duckdb(tmp_path / "db" / "x.duckdb", "published", 1000, duplicate_every=100)
    before = snapshot(tmp_path / "db")
    outcome = _audit(tmp_path, DuckDBTarget(store, "published"), key=("id",), time_column="ts")
    checks = _by_kind(outcome)
    assert checks["data_health.rows"].evidence == "1,000 rows"
    assert checks["data_health.key_unique"].status == Status.FAIL
    assert outcome.report.target_kind == "duckdb"
    assert snapshot(tmp_path / "db") == before


def test_missing_duckdb_table_is_na_with_a_tool_error(tmp_path: Path) -> None:
    store = write_duckdb(tmp_path / "x.duckdb", "published", 10)
    outcome = _audit(tmp_path, DuckDBTarget(store, "nope"))
    assert outcome.tool_errors and "no table nope" in outcome.tool_errors[0]
    assert [c.status for c in outcome.report.checks if c.dimension == "data_health"] == [Status.NA]
    assert outcome.report.score is None


def test_data_age_against_the_limit(tmp_path: Path) -> None:
    write_parquet(tmp_path / "a.parquet", ROWS)  # newest ts 2026-11-24 00:00, 33.5h before NOW
    target = FilesTarget(str(tmp_path / "*.parquet"))
    unjudged = _by_kind(_audit(tmp_path, target, time_column="ts"))["freshness.data_age"]
    assert unjudged.status == Status.NA and "33.5h ago" in unjudged.evidence
    fresh = _by_kind(_audit(tmp_path, target, time_column="ts", max_age_hours=48))
    assert fresh["freshness.data_age"].status == Status.PASS
    stale = _by_kind(_audit(tmp_path, target, time_column="ts", max_age_hours=24))
    assert stale["freshness.data_age"].status == Status.FAIL


# ---- scale guard (spec §6) -------------------------------------------------------------


def test_over_the_file_limit_skips_data_health_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(3):
        write_parquet(tmp_path / "data" / f"{i}.parquet", ROWS)
    monkeypatch.setattr(limits, "MAX_FILES", 2)
    pattern = str(tmp_path / "data" / "*.parquet")
    outcome = _audit(tmp_path, FilesTarget(pattern), key=("id",))
    check = _by_kind(outcome)["data_health.skipped"]
    assert check.id == f"data_health.skipped:{pattern}" and check.status == Status.NA
    assert "3 files" in check.evidence
    notice = outcome.report.notices[0]
    assert notice.startswith(f"{pattern}: 3 files, 0.0 GB.")
    for option in ("--sample 1%", "--since 2026-11-01", "--files", "--full"):
        assert option in notice
    assert "Quick and the other dimensions were evaluated as usual." in notice
    assert outcome.tool_errors == ()


def test_exactly_at_the_limit_is_profiled_and_full_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(3):
        write_parquet(tmp_path / f"{i}.parquet", ROWS)
    target = FilesTarget(str(tmp_path / "*.parquet"))
    monkeypatch.setattr(limits, "MAX_FILES", 3)
    assert "data_health.rows" in _by_kind(_audit(tmp_path, target))
    monkeypatch.setattr(limits, "MAX_FILES", 2)
    full = _audit(tmp_path, target, scan=ScanOptions(full=True))
    assert _by_kind(full)["data_health.rows"].evidence == "12 rows"
    assert full.report.notices == []


def test_byte_and_row_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_parquet(tmp_path / "a.parquet", ROWS)
    monkeypatch.setattr(limits, "MAX_BYTES", 10)
    assert "data_health.skipped" in _by_kind(
        _audit(tmp_path, FilesTarget(str(tmp_path / "*.parquet")))
    )
    store = write_duckdb(tmp_path / "x.duckdb", "t", 1000)
    monkeypatch.setattr(limits, "MAX_ESTIMATED_ROWS", 999)
    skipped = _audit(tmp_path, DuckDBTarget(store, "t"))
    assert "estimated 1,000 rows" in skipped.report.notices[0]
    assert "--files" not in skipped.report.notices[0]


def test_sampled_duplicates_fail_and_a_clean_sample_only_warns(tmp_path: Path) -> None:
    dirty = write_duckdb(tmp_path / "dirty.duckdb", "t", 20_000, duplicate_every=2)
    scan = ScanOptions(sample_percent=100)
    found = _audit(tmp_path, DuckDBTarget(dirty, "t"), key=("id",), scan=scan)
    assert _by_kind(found)["data_health.key_unique"].status == Status.FAIL
    assert "in the sample" in _by_kind(found)["data_health.key_unique"].evidence
    clean = write_duckdb(tmp_path / "clean.duckdb", "t", 20_000)
    scan = ScanOptions(sample_percent=10)
    first = _audit(tmp_path, DuckDBTarget(clean, "t"), key=("id",), scan=scan)
    unique = _by_kind(first)["data_health.key_unique"]
    assert unique.status == Status.WARN and "sample only" in unique.evidence
    assert first.report.sample == "10% of the keys (by key hash)"
    rows = _by_kind(first)["data_health.rows"].evidence
    assert rows.endswith("in the sample") and rows != "20,000 rows in the sample"
    again = _audit(tmp_path, DuckDBTarget(clean, "t"), key=("id",), scan=scan)
    assert json.dumps([c.model_dump(mode="json") for c in again.report.checks]) == json.dumps(
        [c.model_dump(mode="json") for c in first.report.checks]
    )


def test_a_sample_without_a_key_is_repeatable(tmp_path: Path) -> None:
    store = write_duckdb(tmp_path / "x.duckdb", "t", 50_000)
    scan = ScanOptions(sample_percent=5)
    runs = [_by_kind(_audit(tmp_path, DuckDBTarget(store, "t"), scan=scan)) for _ in range(2)]
    assert runs[0]["data_health.rows"].evidence == runs[1]["data_health.rows"].evidence
    assert "50,000" not in runs[0]["data_health.rows"].evidence


def test_a_query_over_the_timeout_is_na_not_fail(tmp_path: Path) -> None:
    store = write_duckdb(tmp_path / "x.duckdb", "t", 3_000_000)
    scan = ScanOptions(timeout_seconds=0.001)
    outcome = _audit(tmp_path, DuckDBTarget(store, "t"), key=("v",), scan=scan)
    check = _by_kind(outcome)["data_health.skipped"]
    assert check.status == Status.NA and check.evidence == "timeout 0.001s"
    assert not outcome.report.has_failures


def test_spill_directory_is_under_censor_tmp_and_left_empty(tmp_path: Path) -> None:
    write_parquet(tmp_path / "data" / "a.parquet", ROWS)
    _audit(tmp_path, FilesTarget(str(tmp_path / "data" / "*.parquet")))
    tmp = tmp_path / ".arsenal" / "censor" / "tmp"
    assert tmp.is_dir() and not list(tmp.iterdir())
    assert sorted(p.name for p in tmp_path.iterdir()) == [".arsenal", "data"]
