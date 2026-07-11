"""에러 분류 체계 — 커넥터가 분류하고, 러너는 타입만 보고 행동한다.

설계: docs/02-architecture.md "에러 분류 체계"
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 1
"""


class ArsenalError(Exception):
    """모든 Arsenal 예외의 루트."""


class FatalError(ArsenalError):
    """재시도 무의미: 설정 오류, 4xx(401/429 제외), 계약 위반. 즉시 중단."""


class RetryableError(ArsenalError):
    """일시적 실패: 네트워크, 5xx, 429, lock 충돌. backoff 재시도."""


class AuthExpiredError(RetryableError):
    """인증 만료. 에러가 아니라 갱신 트리거 (M2에서 refresh hook 연결)."""


def classify_http_status(status: int) -> type[ArsenalError] | None:
    """HTTP 상태 코드를 예외 타입으로 분류. 2xx/3xx는 None.

    401→AuthExpired, 429/5xx→Retryable, 그 외 4xx→Fatal.
    """
    if status < 400:
        return None
    if status == 401:
        return AuthExpiredError
    if status == 429 or status >= 500:
        return RetryableError
    return FatalError
