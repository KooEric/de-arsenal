"""DatabaseSource postgres/mysql — duckdb scanner(ATTACH) 경로 (M2-G).

sqlite3 경로(test_database_source.py)는 TESTED/HARDENED로 그대로 두고, 이 파일은
scanner 경로만 다룬다. postgres는 testcontainers로 실제 컨테이너를 띄워 검증한다
(Docker 가용 — MUST run). mysql은 낮은 우선순위 best-effort: 컨테이너/확장이
준비되지 않으면 명확한 사유로 skip한다.
"""

import urllib.parse
from collections.abc import Iterator

import psycopg
import pyarrow as pa
import pytest

from arsenal_core.errors import ArsenalError, FatalError, RetryableError
from arsenal_core.spec.models import DatabaseSourceSpec, SplitSpec
from pugio.sources.database import DatabaseSource

pgtc = pytest.importorskip("testcontainers.postgres")

DSN_ENV = "PUGIO_TEST_SCANNER_PG_DSN"


@pytest.fixture(scope="module")
def pg_url(require_docker: None) -> Iterator[str]:
    with pgtc.PostgresContainer("postgres:16-alpine") as container:
        url = container.get_connection_url(driver=None)
        with psycopg.connect(url) as con, con.cursor() as cur:
            cur.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, v TEXT)")
            cur.executemany("INSERT INTO orders VALUES (%s, %s)", [(i, "x") for i in range(1, 251)])
            con.commit()
        yield url


@pytest.fixture
def dsn(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(DSN_ENV, pg_url)
    return pg_url


def make_spec(*, chunk: int = 100, table: str = "orders", key: str = "id") -> DatabaseSourceSpec:
    return DatabaseSourceSpec(
        type="database",
        dialect="postgres",
        dsn_env=DSN_ENV,
        table=table,
        split=SplitSpec(key=key, chunk=chunk),
    )


def test_units_are_key_ranges_postgres(dsn: str) -> None:
    src = DatabaseSource(make_spec(), pipeline="p")
    units = list(src.units())
    assert [u.unit_key for u in units] == ["id=1..101", "id=101..201", "id=201..301"]


def test_fetch_range_returns_arrow_postgres(dsn: str) -> None:
    src = DatabaseSource(make_spec(), pipeline="p")
    unit = next(iter(src.units()))
    result = src.fetch(unit)
    assert result.batch is not None
    assert isinstance(result.batch, pa.RecordBatch)
    assert result.batch.num_rows == 100
    assert result.exhausted is False
    assert sorted(row["id"] for row in result.batch.to_pylist()) == list(range(1, 101))


def test_last_partial_range_returns_remaining_rows_postgres(dsn: str) -> None:
    src = DatabaseSource(make_spec(), pipeline="p")
    units = list(src.units())
    result = src.fetch(units[-1])
    assert result.batch is not None
    assert result.batch.num_rows == 50  # 201..250 (범위 끝은 max+1을 넘어감)


def test_fetch_range_with_no_matching_rows_returns_no_batch_postgres(dsn: str) -> None:
    from arsenal_core.state import UnitSpec

    src = DatabaseSource(make_spec(), pipeline="p")
    unit = UnitSpec.create(
        pipeline="p", source="orders", unit_key="id=1000..1100", payload={"lo": 1000, "hi": 1100}
    )
    result = src.fetch(unit)
    assert result.batch is None
    assert result.exhausted is False


def test_missing_table_during_units_query_is_fatal_not_retryable_postgres(dsn: str) -> None:
    """catalog error(테이블 없음)는 설정 오류 — FatalError."""
    src = DatabaseSource(make_spec(table="nonexistent_table"), pipeline="p")
    with pytest.raises(FatalError, match="min/max query failed"):
        list(src.units())


def test_missing_table_during_fetch_is_fatal_not_retryable_postgres(dsn: str) -> None:
    from arsenal_core.state import UnitSpec

    src = DatabaseSource(make_spec(table="nonexistent_table"), pipeline="p")
    unit = UnitSpec.create(
        pipeline="p", source="nonexistent_table", unit_key="id=1..10", payload={"lo": 1, "hi": 10}
    )
    with pytest.raises(FatalError, match="fetch failed for table 'nonexistent_table'"):
        src.fetch(unit)


def test_empty_table_has_no_units_postgres(dsn: str) -> None:
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute("CREATE TABLE empty_orders (id INTEGER PRIMARY KEY, v TEXT)")
        con.commit()
    src = DatabaseSource(make_spec(table="empty_orders"), pipeline="p")
    assert list(src.units()) == []


def test_unreachable_dsn_is_retryable_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """접속 자체가 안 되는 경우(연결 거부)는 duckdb IOException — RetryableError로 분류."""
    monkeypatch.setenv(DSN_ENV, "postgresql://user:pass@127.0.0.1:1/nonexistent")
    src = DatabaseSource(make_spec(), pipeline="p")
    with pytest.raises(RetryableError):
        list(src.units())


def test_missing_dsn_env_is_fatal_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DSN_ENV, raising=False)
    src = DatabaseSource(make_spec(), pipeline="p")
    with pytest.raises(FatalError, match=DSN_ENV):
        list(src.units())


def test_scanner_error_redacts_dsn(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """M2 최종 리뷰 FIX 3 (보안): duckdb의 ATTACH 실패는 연결 문자열을 그대로
    에코백하는데, postgres DSN에는 비밀번호가 들어 있다 — 이 문자열이 그대로
    mark_failed로 state.db에 영속되고 CLI에 출력되면 시크릿이 새어나간다.

    실제 컨테이너(host/port는 유효, 비밀번호만 틀림)로 ATTACH를 실패시켜, 원문
    비밀번호가 예외 메시지에 없고 대신 <redacted-dsn>으로 마스킹됐는지 확인한다.
    """
    parsed = urllib.parse.urlsplit(pg_url)
    wrong_password = "s3cr3t-wrong-password"  # noqa: S105 - 테스트용 가짜 비밀번호, 실제 자격증명 아님
    bad_netloc = f"{parsed.username}:{wrong_password}@{parsed.hostname}:{parsed.port}"
    bad_dsn = urllib.parse.urlunsplit(
        (parsed.scheme, bad_netloc, parsed.path, parsed.query, parsed.fragment)
    )
    monkeypatch.setenv(DSN_ENV, bad_dsn)

    src = DatabaseSource(make_spec(), pipeline="p")
    with pytest.raises(ArsenalError) as exc_info:
        list(src.units())

    message = str(exc_info.value)
    assert wrong_password not in message
    assert bad_dsn not in message
    assert "<redacted-dsn>" in message


# ---- mysql: best-effort, lower priority (task explicitly does not block on it) ----

mysqltc = pytest.importorskip("testcontainers.mysql")

MYSQL_DSN_ENV = "PUGIO_TEST_SCANNER_MYSQL_DSN"


@pytest.fixture(scope="module")
def mysql_url(require_docker: None) -> Iterator[str]:
    import duckdb

    try:
        container = mysqltc.MySqlContainer("mysql:8.0")
        container.start()
    except Exception as e:
        pytest.skip(f"mysql container could not start (best-effort, low priority): {e}")
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(container.port)
        user = container.username
        password = container.password
        dbname = container.dbname
        setup_con = duckdb.connect()
        try:
            setup_con.execute("INSTALL mysql")
            setup_con.execute("LOAD mysql")
            setup_con.execute(
                f"ATTACH 'host={host} port={port} user={user} password={password} "
                f"database={dbname}' AS setup_src (TYPE MYSQL)"
            )
            setup_con.execute("CREATE TABLE setup_src.orders (id INTEGER PRIMARY KEY, v TEXT)")
            setup_con.execute(
                "INSERT INTO setup_src.orders SELECT range::INTEGER + 1, 'x' FROM range(250)"
            )
        except Exception as e:
            pytest.skip(f"mysql duckdb scanner setup failed (best-effort, low priority): {e}")
        finally:
            setup_con.close()
        yield f"host={host} port={port} user={user} password={password} database={dbname}"
    finally:
        container.stop()


@pytest.fixture
def mysql_dsn(mysql_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(MYSQL_DSN_ENV, mysql_url)
    return mysql_url


def make_mysql_spec(*, chunk: int = 100) -> DatabaseSourceSpec:
    return DatabaseSourceSpec(
        type="database",
        dialect="mysql",
        dsn_env=MYSQL_DSN_ENV,
        table="orders",
        split=SplitSpec(key="id", chunk=chunk),
    )


def test_units_are_key_ranges_mysql(mysql_dsn: str) -> None:
    src = DatabaseSource(make_mysql_spec(), pipeline="p")
    units = list(src.units())
    assert [u.unit_key for u in units] == ["id=1..101", "id=101..201", "id=201..301"]


def test_fetch_range_returns_arrow_mysql(mysql_dsn: str) -> None:
    src = DatabaseSource(make_mysql_spec(), pipeline="p")
    unit = next(iter(src.units()))
    result = src.fetch(unit)
    assert result.batch is not None
    assert result.batch.num_rows == 100
    assert sorted(row["id"] for row in result.batch.to_pylist()) == list(range(1, 101))
