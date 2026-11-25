"""Postgres target against a real server (testcontainers); skipped without Docker."""

from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from arsenal_core.errors import FatalError
from censor import limits
from censor.audit import AuditOutcome, AuditRequest, run_audit
from censor.model import CheckResult, Status
from censor.target import PostgresTarget, ScanOptions

pytest.importorskip("testcontainers.postgres")

DSN_ENV = "CENSOR_TEST_PG_DSN"
NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)
ROWS = 200_000


@pytest.fixture(scope="module")
def pg_url(postgres_url: str) -> str:
    with psycopg.connect(postgres_url, autocommit=True) as con:
        con.execute("CREATE TABLE orders (id INTEGER, v TEXT, created_at TIMESTAMPTZ)")
        con.execute(
            "INSERT INTO orders SELECT g, 'v' || g, TIMESTAMPTZ '2026-11-01 00:00:00+00' "
            "+ g * interval '1 second' FROM generate_series(1, %s) g",
            (ROWS,),
        )
        con.execute("CREATE TABLE dirty AS SELECT * FROM orders WHERE id <= 1000")
        con.execute("INSERT INTO dirty SELECT * FROM orders WHERE id <= 3")
        con.execute("ANALYZE orders")
        con.execute("ANALYZE dirty")
        con.execute("CREATE TABLE never_analyzed (id INTEGER)")
        con.execute("INSERT INTO never_analyzed SELECT generate_series(1, 5000)")
        con.execute(
            "CREATE VIEW slow AS SELECT g AS id, pg_sleep(1)::text AS waited "
            "FROM generate_series(1, 30) g"
        )
    return postgres_url


@pytest.fixture
def dsn(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(DSN_ENV, pg_url)
    return pg_url


def _audit(base: Path, table: str, **kw: object) -> AuditOutcome:
    request = AuditRequest(
        root=base,
        tier="standard",
        now=NOW,
        data=PostgresTarget(table, DSN_ENV),
        **kw,  # type: ignore[arg-type]
    )
    return run_audit(request)


def _by_kind(outcome: AuditOutcome) -> dict[str, CheckResult]:
    return {c.id.split(":")[0]: c for c in outcome.report.checks}


def _counters(url: str) -> list[tuple[object, ...]]:
    with psycopg.connect(url) as con:
        con.execute("SELECT pg_stat_clear_snapshot()")
        return con.execute(
            "SELECT relname, n_tup_ins, n_tup_upd, n_tup_del, "
            "(SELECT count(*) FROM orders), (SELECT count(*) FROM dirty) "
            "FROM pg_stat_user_tables ORDER BY relname"
        ).fetchall()


def test_clean_table_passes_and_nothing_is_written(dsn: str, tmp_path: Path) -> None:
    before = _counters(dsn)
    outcome = _audit(tmp_path, "public.orders", key=("id",), time_column="created_at")
    checks = _by_kind(outcome)
    assert checks["data_health.rows"].evidence == f"{ROWS:,} rows"
    assert checks["data_health.key_unique"].status == Status.PASS
    assert checks["data_health.period"].status == Status.PASS
    report = outcome.report
    assert (report.target_kind, report.target) == ("postgres", "postgres:public.orders")
    assert outcome.tool_errors == ()
    assert _counters(dsn) == before


def test_duplicate_key_fails(dsn: str, tmp_path: Path) -> None:
    check = _by_kind(_audit(tmp_path, "dirty", key=("id",)))["data_health.key_unique"]
    assert check.status == Status.FAIL and "3 values of (id)" in check.evidence


def test_estimated_rows_over_the_limit_are_skipped(
    dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(limits, "MAX_ESTIMATED_ROWS", ROWS - 1)
    outcome = _audit(tmp_path, "orders", key=("id",))
    assert _by_kind(outcome)["data_health.skipped"].status == Status.NA
    notice = outcome.report.notices[0]
    assert notice.startswith("postgres:orders:") and "estimated 200,000 rows" in notice
    assert "--sample 1%" in notice and "--files" not in notice
    assert outcome.tool_errors == ()
    full = _audit(tmp_path, "orders", key=("id",), scan=ScanOptions(full=True))
    assert _by_kind(full)["data_health.rows"].status == Status.PASS


def test_a_table_never_analyzed_is_sized_by_bytes(
    dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _by_kind(_audit(tmp_path, "never_analyzed"))["data_health.rows"].evidence == "5,000 rows"
    monkeypatch.setattr(limits, "MAX_BYTES", 1024)
    assert "data_health.skipped" in _by_kind(_audit(tmp_path, "never_analyzed"))


def test_sample_runs_on_the_server_and_repeats(dsn: str, tmp_path: Path) -> None:
    scan = ScanOptions(sample_percent=10)
    first = _audit(tmp_path, "orders", key=("id",), scan=scan)
    again = _audit(tmp_path, "orders", key=("id",), scan=scan)
    rows = _by_kind(first)["data_health.rows"].evidence
    assert rows.endswith("in the sample") and rows != f"{ROWS:,} rows in the sample"
    assert rows == _by_kind(again)["data_health.rows"].evidence
    assert _by_kind(first)["data_health.key_unique"].status == Status.WARN
    assert first.report.sample == "10% of the table blocks (TABLESAMPLE SYSTEM, seed 42)"


def test_since_narrows_the_scan(dsn: str, tmp_path: Path) -> None:
    from datetime import date

    scan = ScanOptions(since=date(2026, 11, 3))
    outcome = _audit(tmp_path, "orders", time_column="created_at", scan=scan)
    rows = int(_by_kind(outcome)["data_health.rows"].evidence.split()[0].replace(",", ""))
    assert 0 < rows < ROWS


def test_statement_timeout_is_na(dsn: str, tmp_path: Path) -> None:
    outcome = _audit(tmp_path, "slow", key=("id",), scan=ScanOptions(timeout_seconds=1))
    check = _by_kind(outcome)["data_health.skipped"]
    assert check.status == Status.NA and check.evidence == "timeout 1s"
    assert not outcome.report.has_failures and outcome.tool_errors == ()


def test_missing_table_is_na_and_a_tool_error(dsn: str, tmp_path: Path) -> None:
    outcome = _audit(tmp_path, "no_such_table", key=("id",))
    assert _by_kind(outcome)["data_health.skipped"].status == Status.NA
    assert outcome.tool_errors == (f"no table no_such_table (DSN from ${DSN_ENV})",)


def test_wrong_password_is_a_tool_error_without_the_dsn(
    pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from urllib.parse import urlsplit

    parts = urlsplit(pg_url)
    bad = pg_url.replace(f":{parts.password}@", ":wr0ng-s3cret@")
    monkeypatch.setenv(DSN_ENV, bad)
    outcome = _audit(tmp_path, "orders")
    text = outcome.report.model_dump_json() + " ".join(outcome.tool_errors)
    assert outcome.tool_errors == (
        f"could not read postgres:orders: authentication failed (DSN from ${DSN_ENV})",
    )
    assert (
        parts.hostname and parts.hostname not in text and "test" not in text.split("DSN")[0][-80:]
    )
    assert "wr0ng-s3cret" not in text and bad not in text and str(parts.port) not in text
    assert _by_kind(outcome)["data_health.skipped"].status == Status.NA


def test_unset_dsn_variable_names_the_variable_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DSN_ENV, raising=False)
    with pytest.raises(FatalError, match=f"environment variable not set: {DSN_ENV}"):
        _audit(tmp_path, "orders")
