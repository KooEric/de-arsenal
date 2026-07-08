"""운영 DB 소스 — 커넥터를 만들지 않는다. DuckDB scanner(ATTACH)가 드라이버·타입
매핑·전송을 담당하고, 우리는 키 범위 unit 분할 + 재개·멱등만 얹는다.

DE의 1번 수집 작업(운영 DB → 웨어하우스 동기화). 늘어난 행은 재실행 시
max(key) 재조회로 새 unit이 생겨 증분 동기화가 구조에서 공짜로 나온다.
UPDATE된 기존 행은 P0 범위 밖(스냅샷 의미론).
구현: docs/plans/2026-07-08-m2-pugio-complete.md Task 2.12 / 전략: docs/09-oss-leverage.md 수 1
"""

from collections.abc import Iterator

from arsenal_core.spec.models import DatabaseSourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult


class DatabaseSource:
    def __init__(self, spec: DatabaseSourceSpec, *, pipeline: str) -> None:
        self._spec = spec
        self._pipeline = pipeline

    def units(self) -> Iterator[UnitSpec]:
        """min/max(key) 조회 → chunk 단위 범위 열거. unit_key = "id=lo..hi" (결정적)."""
        raise NotImplementedError("M2 Task 2.12 — docs/plans/2026-07-08-m2-pugio-complete.md")

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """duckdb ATTACH (READ_ONLY) → 범위 SELECT → Arrow."""
        raise NotImplementedError("M2 Task 2.12 — docs/plans/2026-07-08-m2-pugio-complete.md")
