import urllib.parse

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
def pg_url(postgres_url: str) -> str:
    return postgres_url


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


def test_naive_timestamp_is_stored_as_written(dsn: str) -> None:
    """tz 없는 timestamp는 `timestamp`(without time zone) 컬럼에 적힌 시각 그대로 들어간다.

    timestamptz에 넣으면 세션 타임존으로 해석돼 값이 shift된다 — 세션 타임존을 바꿔도
    읽은 값이 같아야 한다.
    """
    import datetime

    sink = make_sink("contract_naive_ts")
    written = datetime.datetime(2026, 1, 1, 9, 30)  # noqa: DTZ001 — naive가 테스트 대상
    batch = pa.RecordBatch.from_pylist(
        [{"id": 1, "seen_at": written}],
        schema=pa.schema([("id", pa.int64()), ("seen_at", pa.timestamp("s"))]),
    )
    sink.write(make_unit("u"), batch)

    with psycopg.connect(dsn) as con:
        con.execute("SET TIME ZONE 'Asia/Seoul'")
        row = con.execute("SELECT seen_at FROM contract_naive_ts").fetchone()
        kind = con.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'contract_naive_ts' AND column_name = 'seen_at'"
        ).fetchone()
    assert row == (written,)
    assert kind == ("timestamp without time zone",)


def test_csv_shaped_types_round_trip(dsn: str) -> None:
    """A plain CSV infers date, small ints, decimals and all-empty columns."""
    import datetime
    from decimal import Decimal

    sink = make_sink("contract_csv_types")
    schema = pa.schema(
        [
            ("id", pa.int64()),
            ("day", pa.date32()),
            ("qty", pa.int32()),
            ("flag", pa.int8()),
            ("ratio", pa.float32()),
            ("price", pa.decimal128(10, 2)),
            ("at", pa.time64("us")),
            ("blob", pa.binary()),
            ("empty", pa.null()),
        ]
    )
    record = {
        "id": 1,
        "day": datetime.date(2026, 11, 1),
        "qty": 3,
        "flag": 1,
        "ratio": 0.5,
        "price": Decimal("42.50"),
        "at": datetime.time(9, 30),
        "blob": b"\x00\x01",
        "empty": None,
    }
    sink.write(make_unit("u"), pa.RecordBatch.from_pylist([record], schema=schema))
    sink.write(make_unit("u"), pa.RecordBatch.from_pylist([record], schema=schema))

    assert readback(dsn, "contract_csv_types") == [record]


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
    batch = pa.RecordBatch.from_arrays(
        [pa.array([1]), pa.array([[1, 2]], type=pa.list_(pa.int64()))], names=["id", "tags"]
    )
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
