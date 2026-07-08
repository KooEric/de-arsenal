"""인증 계층 (M2) — 만료는 에러가 아니라 갱신 트리거.

계획 (docs/01-scope.md M2 Task 2.4):
- AuthProvider 프로토콜: 요청 헤더 공급 + refresh() 훅
- 구현체: static token / oauth2 client credentials(만료 전 선제 갱신) / 커스텀 훅
- 러너 연동: 401 → AuthExpiredError → provider.refresh() → 같은 unit 재시도

M1에서는 spec.headers의 정적 헤더로 충분하므로 프로토콜만 예약해 둔다.
"""

from typing import Protocol


class AuthProvider(Protocol):
    def headers(self) -> dict[str, str]:
        """현재 유효한 인증 헤더."""
        ...

    def refresh(self) -> None:
        """자격 증명 갱신. AuthExpiredError 수신 시 러너가 호출한다 (M2)."""
        ...


__all__ = ["AuthProvider"]
