import urllib.parse
from collections.abc import Iterator

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
from arsenal_core.spec.models import PostgresSinkSpec
from pugio.sinks.postgres import PostgresSink

pgtc = pytest.importorskip("testcontainers.postgres")

DSN_ENV = "PUGIO_TEST_PG_DSN"


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    with pgtc.PostgresContainer("postgres:16-alpine") as container:
        yield container.get_connection_url(driver=None)


@pytest.fixture
def dsn(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(DSN_ENV, pg_url)
    return pg_url


def make_sink(table: str, *, merge_key: list[str] | None = None) -> PostgresSink:
    spec = PostgresSinkSpec(
        type="postgres", dsn_env=DSN_ENV, table=table, merge_key=merge_key or ["id"]
    )
    return PostgresSink(spec)


def readback(dsn: str, table: str) -> list[dict[str, object]]:
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
        cols = [d.name for d in cur.description or []]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def test_write_twice_same_unit_row_count_unchanged(dsn: str) -> None:
    sink = make_sink("contract_idem")
    assert_write_twice_same_unit_is_idempotent(sink, lambda: readback(dsn, "contract_idem"))


def test_merge_updates_changed_rows(dsn: str) -> None:
    sink = make_sink("contract_merge")
    assert_merge_updates_changed_rows(sink, lambda: readback(dsn, "contract_merge"))


def test_write_two_different_batches_accumulates(dsn: str) -> None:
    sink = make_sink("contract_accum")
    assert_two_different_batches_accumulate(sink, lambda: readback(dsn, "contract_accum"))


def test_in_batch_duplicate_key_keeps_last(dsn: str) -> None:
    sink = make_sink("contract_dup")
    assert_in_batch_duplicate_key_keeps_last(sink, lambda: readback(dsn, "contract_dup"))


def test_composite_merge_key_upsert(dsn: str) -> None:
    sink = make_sink("contract_composite", merge_key=["a", "b"])
    assert_composite_merge_key_upsert(sink, lambda: readback(dsn, "contract_composite"))


def test_bad_password_is_fatal_not_retryable(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """실행 중인 컨테이너의 host/port는 그대로 두고 비밀번호만 틀리게 만들면
    psycopg.OperationalError의 sqlstate가 28P01(invalid_password)이어야 한다 —
    설정 오류라 재시도해도 소용없으니 FatalError여야 한다 (RetryableError가
    아니다) (M2-F FIX 3)."""
    parsed = urllib.parse.urlsplit(pg_url)
    bad_netloc = f"{parsed.username}:wrong-password@{parsed.hostname}:{parsed.port}"
    bad_dsn = urllib.parse.urlunsplit(
        (parsed.scheme, bad_netloc, parsed.path, parsed.query, parsed.fragment)
    )
    monkeypatch.setenv("PUGIO_TEST_PG_BADPW", bad_dsn)
    spec = PostgresSinkSpec(
        type="postgres", dsn_env="PUGIO_TEST_PG_BADPW", table="x", merge_key=["id"]
    )
    sink = PostgresSink(spec)
    with pytest.raises(FatalError):
        sink.write(make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}]))


def test_malformed_dsn_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """M2 최종 리뷰 FIX 5: connect() 주변에서 psycopg.OperationalError만 잡으면
    문법이 잘못된 DSN이 내는 psycopg.ProgrammingError가 분류 없이 그대로
    escape한다 — 재시도해도 문법은 그대로 틀린 채이므로 FatalError여야 한다."""
    monkeypatch.setenv("PUGIO_TEST_PG_MALFORMED", "this is not a dsn at all!!")
    spec = PostgresSinkSpec(
        type="postgres", dsn_env="PUGIO_TEST_PG_MALFORMED", table="x", merge_key=["id"]
    )
    sink = PostgresSink(spec)
    with pytest.raises(FatalError):
        sink.write(make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}]))


def test_naive_timestamp_is_fatal(dsn: str) -> None:
    """tz 정보가 없는 timestamp 컬럼은 postgres에 꽂히는 순간 서버 세션 타임존
    기준으로 암묵 해석되어 값이 조용히 shift될 수 있다 — 업스트림에서 UTC로
    정규화하도록 강제하며 FatalError로 막는다 (M2-F FIX 6)."""
    import datetime

    sink = make_sink("contract_naive_ts")
    batch = pa.RecordBatch.from_pylist(
        [{"id": 1, "seen_at": datetime.datetime(2026, 1, 1)}],  # noqa: DTZ001 — naive가 테스트 대상
        schema=pa.schema([("id", pa.int64()), ("seen_at", pa.timestamp("us"))]),
    )
    with pytest.raises(FatalError, match="naive timestamp"):
        sink.write(make_unit("u"), batch)


def test_missing_dsn_env_raises_fatal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUGIO_TEST_PG_MISSING", raising=False)
    spec = PostgresSinkSpec(
        type="postgres", dsn_env="PUGIO_TEST_PG_MISSING", table="x", merge_key=["id"]
    )
    sink = PostgresSink(spec)
    with pytest.raises(FatalError, match="PUGIO_TEST_PG_MISSING"):
        sink.write(make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}]))


def test_unreachable_dsn_raises_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "PUGIO_TEST_PG_UNREACHABLE", "postgresql://user:pass@127.0.0.1:1/nonexistent"
    )
    spec = PostgresSinkSpec(
        type="postgres", dsn_env="PUGIO_TEST_PG_UNREACHABLE", table="x", merge_key=["id"]
    )
    sink = PostgresSink(spec)
    with pytest.raises(RetryableError):
        sink.write(make_unit("u"), pa.RecordBatch.from_pylist([{"id": 1}]))


def test_unsupported_arrow_type_raises_fatal_error(dsn: str) -> None:
    sink = make_sink("contract_badtype")
    batch = pa.RecordBatch.from_arrays([pa.array([1], type=pa.int32())], names=["id"])
    with pytest.raises(FatalError, match="unsupported arrow type"):
        sink.write(make_unit("u"), batch)


def test_all_supported_arrow_types_round_trip(dsn: str) -> None:
    """PINNED 타입 맵: int64→bigint, string/large_string→text, double→double
    precision, bool→boolean, timestamp→timestamptz. 실제 Postgres에 써서 값이
    그대로 돌아오는지 확인한다 — 컬럼 자체가 merge_key가 아닌 일반 컬럼일 때
    DO UPDATE SET 경로도 함께 exercise된다."""
    import datetime

    sink = make_sink("contract_types")
    ts = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    batch = pa.RecordBatch.from_pylist(
        [
            {
                "id": 1,
                "name": "a",
                "big_name": "b" * 5,
                "score": 1.5,
                "active": True,
                "seen_at": ts,
            }
        ],
        schema=pa.schema(
            [
                ("id", pa.int64()),
                ("name", pa.string()),
                ("big_name", pa.large_string()),
                ("score", pa.float64()),
                ("active", pa.bool_()),
                ("seen_at", pa.timestamp("us", tz="UTC")),
            ]
        ),
    )
    sink.write(make_unit("u"), batch)
    rows = readback(dsn, "contract_types")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1
    assert row["name"] == "a"
    assert row["big_name"] == "bbbbb"
    assert row["score"] == 1.5
    assert row["active"] is True
    assert row["seen_at"] == ts
