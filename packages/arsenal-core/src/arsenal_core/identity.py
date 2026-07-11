"""결정적 Unit ID — 멱등성의 근거. 같은 unit은 언제 실행해도 같은 ID.

설계: docs/02-architecture.md "결정적 ID"
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 2
"""

import hashlib

_ID_LEN = 16


def unit_id(pipeline: str, source: str, unit_key: str) -> str:
    """sha256("{pipeline}\\x1f{source}\\x1f{unit_key}") 앞 16 hex.

    \\x1f 구분자로 경계 모호성 제거. 결정성·충돌 저항은 hypothesis로 검증.
    """
    raw = f"{pipeline}\x1f{source}\x1f{unit_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:_ID_LEN]
