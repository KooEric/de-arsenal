"""식별자 안전 처리 — quoting + cast 타입 화이트리스트 (M3 Task 3.2).

경계: filter/derive/map의 expression은 사용자가 작성하는 SQL 조각이다 — 사용자
자신의 쿼리이므로 인젝션 방어 대상이 아니라 문서화 대상(dbt와 동일한 신뢰 모델).
방어 대상은 식별자·타입처럼 구조를 깨는 위치다.
"""

from arsenal_core.errors import FatalError


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


_CAST_TYPES = {  # cast step 타입 화이트리스트 — 임의 문자열을 타입 위치에 두지 않는다
    "bigint": "BIGINT",
    "integer": "INTEGER",
    "double": "DOUBLE",
    "varchar": "VARCHAR",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "decimal": "DECIMAL(38, 9)",
}


def normalize_type(t: str) -> str:
    try:
        return _CAST_TYPES[t.lower()]
    except KeyError:
        raise FatalError(f"unsupported cast type: {t!r}") from None
