"""운영 DB 소스 — 키 범위 unit 분할 + 재개·멱등.

dialect="sqlite"는 네트워크 의존 없이 즉시 동작하는 Python 표준 sqlite3 모듈
경로(TESTED/HARDENED, 아래에서 동작을 바꾸지 않는다). dialect="postgres"/"mysql"은
DuckDB scanner(ATTACH)로 드라이버·타입 매핑을 위임한다 (M2-G, docs/09-oss-leverage.md
수 1). 두 경로 모두 min/max(key) → chunk 단위 범위 열거라는 동일한 계산을 공유하며
(`_key_ranges`), fetch만 드라이버별로 갈린다.

DE의 1번 수집 작업(운영 DB → 웨어하우스 동기화). 늘어난 행은 재실행 시
max(key) 재조회로 새 unit이 생겨 증분 동기화가 구조에서 공짜로 나온다.
UPDATE된 기존 행은 범위 밖(스냅샷 의미론).
구현: docs/plans/2026-07-08-m2-pugio-complete.md Task 2.12 / 전략: docs/09-oss-leverage.md 수 1
"""

import os
import re
import sqlite3
from collections.abc import Iterator
from typing import NoReturn

import duckdb
import pyarrow as pa

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import DatabaseSourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRANSIENT_MARKERS = ("database is locked", "database is busy")

# dialect → (duckdb 확장 이름, ATTACH TYPE 키워드). Literal["postgres","mysql","sqlite"]의
# sqlite를 뺀 두 값만 scanner 경로로 온다 (units()/fetch()에서 분기).
_SCANNER_EXTENSION = {"postgres": "postgres", "mysql": "mysql"}
_SCANNER_ATTACH_TYPE = {"postgres": "POSTGRES", "mysql": "MYSQL"}

# duckdb 에러 분류 (M2-F DuckDBSink와 동일한 원칙): IO/커넥션/트랜잭션 충돌만
# 일시적 — 서버 다운, 네트워크 단절, lock 경합 등은 재시도하면 성공할 수 있다.
# catalog(테이블 없음)/binder(컬럼 없음)/parser 등 나머지는 설정 오류라 FatalError.
_DUCKDB_RETRYABLE: tuple[type[duckdb.Error], ...] = (
    duckdb.IOException,
    duckdb.ConnectionException,
    duckdb.TransactionException,
)


def _qi(name: str) -> str:
    """식별자를 안전하게 인용 — 영숫자/언더스코어만 허용해 SQL 인젝션을 막는다."""
    if not _IDENT_RE.match(name):
        raise FatalError(f"invalid identifier: {name!r}")
    return f'"{name}"'


def _raise_classified(e: sqlite3.Error, *, context: str) -> NoReturn:
    """sqlite3 에러를 재시도 가능 여부로 분류해 던진다.

    OperationalError이면서 "database is locked/busy"인 경우만 일시적(RetryableError).
    테이블/컬럼 없음, SQL 오류 등은 설정 오류라 재시도해도 해결되지 않는다(FatalError).
    """
    if isinstance(e, sqlite3.OperationalError) and any(
        marker in str(e).lower() for marker in _TRANSIENT_MARKERS
    ):
        raise RetryableError(f"{context}: {e}") from e
    raise FatalError(f"{context}: {e}") from e


def _raise_classified_duckdb(e: duckdb.Error, *, context: str, dsn: str | None = None) -> NoReturn:
    """duckdb scanner 에러를 재시도 가능 여부로 분류해 던진다 (M2-F DuckDBSink와 동일 원칙).

    dsn이 주어지면(M2 최종 리뷰 FIX 3) 에러 메시지에서 DSN 원문을 마스킹한다 —
    ATTACH 실패 시 duckdb가 연결 문자열을 그대로 에코하는 경우가 있는데, postgres/
    mysql DSN에는 비밀번호가 포함될 수 있다. 이 메시지는 mark_failed로 state.db에
    영속되고 CLI에도 그대로 출력되므로 비밀만 마스킹하고 나머지 에러 상세는
    보존한다.
    """
    message = str(e)
    if dsn:
        message = message.replace(dsn, "<redacted-dsn>")
    if isinstance(e, _DUCKDB_RETRYABLE):
        raise RetryableError(f"{context}: {message}") from e
    raise FatalError(f"{context}: {message}") from e


def _key_ranges(
    lo: int, hi_bound: int, chunk: int, *, pipeline: str, source: str, key: str
) -> Iterator[UnitSpec]:
    """min/max(key) 범위를 chunk 단위로 잘라 UnitSpec을 낸다 — sqlite/scanner 공통.

    dialect-agnostic: min/max만 구하면 그 다음 chunk 계산은 드라이버와 무관하다.
    unit_key = "id=lo..hi" (결정적 — 재실행 시 동일 unit_id로 done 여부를 판별).
    """
    lo_cursor = lo
    while lo_cursor <= hi_bound:
        hi = lo_cursor + chunk
        yield UnitSpec.create(
            pipeline=pipeline,
            source=source,
            unit_key=f"{key}={lo_cursor}..{hi}",
            payload={"lo": lo_cursor, "hi": hi},
        )
        lo_cursor = hi


class DatabaseSource:
    def __init__(self, spec: DatabaseSourceSpec, *, pipeline: str) -> None:
        self._spec = spec
        self._pipeline = pipeline

    def _dsn(self) -> str:
        dsn = os.environ.get(self._spec.dsn_env)
        if dsn is None:
            raise FatalError(f"environment variable not set: {self._spec.dsn_env}")
        return dsn

    # ---- sqlite3 경로 (TESTED/HARDENED — 동작 불변) ----------------------------

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._dsn())

    def _units_sqlite(self) -> Iterator[UnitSpec]:
        key = self._spec.split.key
        chunk = self._spec.split.chunk
        con = self._connect()
        try:
            row = con.execute(
                f"SELECT min({_qi(key)}), max({_qi(key)}) FROM {_qi(self._spec.table)}"
            ).fetchone()
        except sqlite3.Error as e:
            _raise_classified(e, context=f"min/max query failed for table {self._spec.table!r}")
        finally:
            con.close()
        if row is None or row[0] is None or row[1] is None:
            return
        yield from _key_ranges(
            row[0], row[1], chunk, pipeline=self._pipeline, source=self._spec.table, key=key
        )

    def _fetch_sqlite(self, unit: UnitSpec) -> FetchResult:
        key = self._spec.split.key
        lo, hi = unit.payload["lo"], unit.payload["hi"]
        con = self._connect()
        try:
            cur = con.execute(
                f"SELECT * FROM {_qi(self._spec.table)} WHERE {_qi(key)} >= ? AND {_qi(key)} < ?",
                (lo, hi),
            )
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
        except sqlite3.Error as e:
            _raise_classified(e, context=f"fetch failed for table {self._spec.table!r}")
        finally:
            con.close()
        if not rows:
            return FetchResult(batch=None, exhausted=False)
        records = [dict(zip(columns, r, strict=True)) for r in rows]
        return FetchResult(batch=pa.RecordBatch.from_pylist(records), exhausted=False)

    # ---- postgres/mysql: duckdb scanner 경로 (M2-G) ----------------------------

    def _scanner_connect(self) -> duckdb.DuckDBPyConnection:
        """duckdb 확장 설치/로드 + ATTACH. dsn은 파라미터화 불가(ATTACH는 DDL)라
        f-string으로 조립하되, 작은따옴표만 이스케이프해 문법 파손을 막는다
        (dsn은 dsn_env로만 주입되는 운영자 설정값 — 비밀 원칙, 사용자 입력 아님).
        """
        dialect = self._spec.dialect
        extension = _SCANNER_EXTENSION[dialect]
        attach_type = _SCANNER_ATTACH_TYPE[dialect]
        raw_dsn = self._dsn()
        dsn = raw_dsn.replace("'", "''")
        con = duckdb.connect()
        try:
            con.execute(f"INSTALL {extension}")
            con.execute(f"LOAD {extension}")
            con.execute(f"ATTACH '{dsn}' AS src (TYPE {attach_type}, READ_ONLY)")
        except duckdb.Error as e:
            con.close()
            _raise_classified_duckdb(
                e, context=f"attach failed for dialect {dialect!r}", dsn=raw_dsn
            )
        except Exception:
            con.close()
            raise
        return con

    def _units_scanner(self) -> Iterator[UnitSpec]:
        key = self._spec.split.key
        chunk = self._spec.split.chunk
        con = self._scanner_connect()
        try:
            row = con.execute(
                f"SELECT min({_qi(key)}), max({_qi(key)}) FROM src.{_qi(self._spec.table)}"
            ).fetchone()
        except duckdb.Error as e:
            _raise_classified_duckdb(
                e,
                context=f"min/max query failed for table {self._spec.table!r}",
                dsn=self._dsn(),
            )
        finally:
            con.close()
        if row is None or row[0] is None or row[1] is None:
            return
        yield from _key_ranges(
            row[0], row[1], chunk, pipeline=self._pipeline, source=self._spec.table, key=key
        )

    def _fetch_scanner(self, unit: UnitSpec) -> FetchResult:
        key = self._spec.split.key
        lo, hi = unit.payload["lo"], unit.payload["hi"]
        con = self._scanner_connect()
        table: pa.Table
        try:
            result = con.execute(
                f"SELECT * FROM src.{_qi(self._spec.table)} "
                f"WHERE {_qi(key)} >= ? AND {_qi(key)} < ?",
                [lo, hi],
            )
            table = result.to_arrow_table()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        except duckdb.Error as e:
            _raise_classified_duckdb(
                e, context=f"fetch failed for table {self._spec.table!r}", dsn=self._dsn()
            )
        finally:
            con.close()
        if table.num_rows == 0:  # pyright: ignore[reportUnknownMemberType]
            return FetchResult(batch=None, exhausted=False)
        batches = table.combine_chunks().to_batches()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return FetchResult(batch=batches[0], exhausted=False)  # pyright: ignore[reportUnknownArgumentType]

    # ---- dispatch ---------------------------------------------------------

    def units(self) -> Iterator[UnitSpec]:
        """min/max(key) 조회 → chunk 단위 범위 열거. dialect로 sqlite/scanner 분기."""
        if self._spec.dialect == "sqlite":
            yield from self._units_sqlite()
        else:
            yield from self._units_scanner()

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """범위 조회 → Arrow. dialect로 sqlite/scanner 분기, 에러 분류는 각자 담당."""
        if self._spec.dialect == "sqlite":
            return self._fetch_sqlite(unit)
        return self._fetch_scanner(unit)
