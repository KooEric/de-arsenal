"""인증 계층 (M2-D) — 만료는 에러가 아니라 갱신 트리거.

계획 (docs/01-scope.md M2 Task 2.4):
- AuthProvider 프로토콜: 요청 헤더 공급 + refresh() 훅
- 구현체: static token / oauth2 client credentials(만료 전 선제 갱신)
- 러너 연동: 401 → AuthExpiredError → provider.refresh() → 같은 unit 재시도 (runner.py)
"""

import os
import time
from typing import Protocol

import httpx

from arsenal_core.errors import FatalError
from arsenal_core.ratelimit import Clock
from arsenal_core.spec.models import AuthSpec


class AuthProvider(Protocol):
    def headers(self) -> dict[str, str]:
        """현재 유효한 인증 헤더."""
        ...

    def refresh(self) -> None:
        """자격 증명 갱신. AuthExpiredError 수신 시 러너가 호출한다."""
        ...


class _MonotonicClock:
    """arsenal_core.ratelimit._MonotonicClock와 동일한 shape — arsenal_core는 이 클래스를
    export하지 않으므로(모듈 내부 전용) 여기서 별도로 둔다."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class StaticTokenAuth:
    """정적 토큰 — 환경변수에서 매번 읽는다 (회전 시 프로세스 재시작 없이 반영).

    갱신 불가: 만료된 정적 토큰은 사람이 시크릿을 교체해야 하므로 refresh()는
    FatalError로 즉시 중단한다 (재시도해도 나아지지 않음).
    """

    def __init__(self, token_env: str) -> None:
        self._env = token_env

    def headers(self) -> dict[str, str]:
        try:
            token = os.environ[self._env]
        except KeyError as e:
            raise FatalError(f"static token env var {self._env!r} is not set") from e
        return {"Authorization": f"Bearer {token}"}

    def refresh(self) -> None:
        raise FatalError("static token expired; rotate the secret")


class OAuth2ClientCredentials:
    """client_credentials 그랜트 — expiry_buffer_s 전에 선제 재발급.

    headers()는 토큰이 없거나 만료(버퍼 포함)에 가까우면 자동으로 재발급한다.
    refresh()는 (버퍼와 무관하게) 즉시 강제 재발급 — 401 수신 시 러너가 호출.
    Clock은 테스트에서 FakeClock으로 주입해 시간 경과를 결정론적으로 검증한다.
    """

    def __init__(
        self,
        spec: AuthSpec,
        *,
        client: httpx.Client,
        clock: Clock | None = None,
    ) -> None:
        self._spec = spec
        self._client = client
        self._clock = clock or _MonotonicClock()
        self._token: str | None = None
        self._expiry: float = 0.0

    def headers(self) -> dict[str, str]:
        now = self._clock.monotonic()
        if self._token is None or now > self._expiry - self._spec.expiry_buffer_s:
            self._fetch_token()
        return {"Authorization": f"Bearer {self._token}"}

    def refresh(self) -> None:
        self._fetch_token()

    def _fetch_token(self) -> None:
        if self._spec.token_url is None:
            raise FatalError("oauth2 auth requires token_url")
        if self._spec.client_id_env is None or self._spec.client_secret_env is None:
            raise FatalError("oauth2 auth requires client_id_env and client_secret_env")
        try:
            client_id = os.environ[self._spec.client_id_env]
            client_secret = os.environ[self._spec.client_secret_env]
        except KeyError as e:
            raise FatalError(f"oauth2 credential env var missing: {e}") from e
        resp = self._client.post(
            self._spec.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if resp.status_code >= 400:
            # 토큰 엔드포인트 실패는 설정/자격증명 오류 — 재시도해도 나아지지 않는다.
            raise FatalError(
                f"token request to {self._spec.token_url} failed: "
                f"{resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        try:
            access_token = body["access_token"]
            expires_in = body["expires_in"]
        except KeyError as e:
            raise FatalError(f"token response missing field: {e}") from e
        self._token = access_token
        self._expiry = self._clock.monotonic() + expires_in


def build_auth(
    spec: AuthSpec | None,
    client: httpx.Client,
    *,
    clock: Clock | None = None,
) -> AuthProvider | None:
    """spec.type으로 알맞은 AuthProvider를 만든다. spec이 None이면 None (인증 없음)."""
    if spec is None:
        return None
    if spec.type == "static_token":
        if spec.token_env is None:
            raise FatalError("static_token auth requires token_env")
        return StaticTokenAuth(spec.token_env)
    return OAuth2ClientCredentials(spec, client=client, clock=clock)


__all__ = ["AuthProvider", "StaticTokenAuth", "OAuth2ClientCredentials", "build_auth"]
