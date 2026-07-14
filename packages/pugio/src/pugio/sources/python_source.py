"""Python 커스텀 소스 탈출구 — YAML로 표현 안 되는 API를 만나도 절벽이 없다.

사용자의 Source 프로토콜 구현체를 동적 로드한다. dbt 매크로와 같은 신뢰 모델
(target은 사용자 자신의 코드 — 원격/서드파티 target을 받는 서비스화는 범위 밖).
P1 dlt 래퍼(`type: dlt`)도 이 메커니즘 위에 선다.
구현: docs/plans/2026-07-08-m2-pugio-complete.md Task 2.13
"""

import importlib
from typing import Any

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import PythonSourceSpec
from pugio.sources.base import Source


def load_python_source(spec: PythonSourceSpec, *, pipeline: str) -> Source:
    """ "pkg.module:ClassName"을 import → 프로토콜(units/fetch) 검사 → 인스턴스화.

    import 실패·프로토콜 미구현·생성자 예외 모두 FatalError (설정/사용자 코드 오류 —
    재시도 무의미). 사용자 코드 import·인스턴스화는 임의 예외를 던질 수 있으므로
    Exception 전체를 잡아 target을 이름으로 문 FatalError로 재포장한다.
    """
    module_name, sep, cls_name = spec.target.partition(":")
    if not sep:
        raise FatalError(
            f"invalid python source target {spec.target!r}: expected 'module:ClassName'"
        )
    try:
        module = importlib.import_module(module_name)
        cls: Any = getattr(module, cls_name)
        for method in ("units", "fetch"):
            if not callable(getattr(cls, method, None)):
                raise FatalError(f"{spec.target} does not implement Source protocol: {method}")
        return cls(spec.options, pipeline=pipeline)
    except FatalError:
        raise
    except Exception as e:
        raise FatalError(f"cannot load python source {spec.target!r}: {e}") from e
