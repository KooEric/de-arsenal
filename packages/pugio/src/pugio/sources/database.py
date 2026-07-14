"""운영 DB 소스 — 키 범위 unit 분할 + 재개·멱등.

**초안(draft) 범위 이탈 고지**: 원안(docs/plans/2026-07-08-m2-pugio-complete.md
Task 2.12)은 DuckDB scanner(ATTACH)로 드라이버·타입 매핑을 위임하지만, 이 초안은
네트워크 의존(확장 다운로드) 없이 즉시 동작하도록 Python 표준 sqlite3 모듈만 사용한다.
dialect="postgres"/"mysql"은 아직 구현체가 없다 — 생성 시점에 FatalError.

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

import pyarrow as pa

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import DatabaseSourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRANSIENT_MARKERS = ("database is locked", "database is busy")


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


class DatabaseSource:
    def __init__(self, spec: DatabaseSourceSpec, *, pipeline: str) -> None:
        if spec.dialect != "sqlite":
            raise FatalError(
                f"DatabaseSource draft only supports dialect='sqlite' (got {spec.dialect!r}); "
                "postgres/mysql are P1 scope (docs/09-oss-leverage.md)"
            )
        self._spec = spec
        self._pipeline = pipeline

    def _dsn(self) -> str:
        dsn = os.environ.get(self._spec.dsn_env)
        if dsn is None:
            raise FatalError(f"environment variable not set: {self._spec.dsn_env}")
        return dsn

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._dsn())

    def units(self) -> Iterator[UnitSpec]:
        """min/max(key) 조회 → chunk 단위 범위 열거. unit_key = "id=lo..hi" (결정적)."""
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
        lo, hi_bound = row[0], row[1]
        while lo <= hi_bound:
            hi = lo + chunk
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.table,
                unit_key=f"{key}={lo}..{hi}",
                payload={"lo": lo, "hi": hi},
            )
            lo = hi

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """sqlite3 SELECT 범위 조회 → Arrow. lock 충돌만 RetryableError, 나머지는 FatalError."""
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
