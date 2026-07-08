"""Python 커스텀 소스 탈출구 — YAML로 표현 안 되는 API를 만나도 절벽이 없다.

사용자의 Source 프로토콜 구현체를 동적 로드한다. dbt 매크로와 같은 신뢰 모델
(target은 사용자 자신의 코드). P1 dlt 래퍼(`type: dlt`)도 이 메커니즘 위에 선다.
구현: docs/plans/2026-07-08-m2-pugio-complete.md Task 2.13
"""

from arsenal_core.spec.models import PythonSourceSpec
from pugio.sources.base import Source


def load_python_source(spec: PythonSourceSpec, *, pipeline: str) -> Source:
    """ "pkg.module:ClassName"을 import → 프로토콜(units/fetch) 검사 → 인스턴스화.

    import 실패·프로토콜 미구현은 FatalError (설정 오류 — 재시도 무의미).
    """
    raise NotImplementedError("M2 Task 2.13 — docs/plans/2026-07-08-m2-pugio-complete.md")
