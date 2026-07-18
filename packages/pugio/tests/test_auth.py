"""M2-D: 인증 프로바이더 — static token / oauth2 client_credentials.

만료는 에러가 아니라 갱신 트리거라는 설계를 검증한다:
- StaticTokenAuth: 환경변수 → Bearer 헤더. 갱신 불가(FatalError).
- OAuth2ClientCredentials: expiry_buffer_s 전에 선제 재발급. FakeClock으로
  실제 sleep 없이 시간 경과를 결정론적으로 검증한다 (test_ratelimit.py 패턴 차용).
"""

from pathlib import Path

import httpx
import pytest
import respx

from arsenal_core.errors import FatalError
from arsenal_core.ratelimit import Clock
from arsenal_core.spec.models import (
    AuthSpec,
    PaginationSpec,
    ParquetSinkSpec,
    PipelineSpec,
    RestSourceSpec,
)
from pugio.auth import OAuth2ClientCredentials, StaticTokenAuth, build_auth
from pugio.runner import run_pipeline


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:  # pragma: no cover - Clock 프로토콜 충족용
        self.now += seconds


def test_static_token_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "secret-abc")
    auth = StaticTokenAuth("MY_TOKEN")
    assert auth.headers() == {"Authorization": "Bearer secret-abc"}


def test_static_token_missing_env_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    auth = StaticTokenAuth("MISSING_TOKEN")
    with pytest.raises(FatalError):
        auth.headers()


def test_static_refresh_is_fatal() -> None:
    auth = StaticTokenAuth("ANY_TOKEN")
    with pytest.raises(FatalError):
        auth.refresh()


@respx.mock
def test_oauth2_fetches_and_caches_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_ID", "cid")
    monkeypatch.setenv("OAUTH_SECRET", "csecret")
    route = respx.post("https://auth.test/token").respond(
        json={"access_token": "tok-1", "expires_in": 3600}
    )
    spec = AuthSpec(
        type="oauth2_client_credentials",
        token_url="https://auth.test/token",
        client_id_env="OAUTH_ID",
        client_secret_env="OAUTH_SECRET",
    )
    auth = OAuth2ClientCredentials(spec, client=httpx.Client())
    headers1 = auth.headers()
    headers2 = auth.headers()
    assert headers1 == {"Authorization": "Bearer tok-1"}
    assert headers2 == {"Authorization": "Bearer tok-1"}
    assert route.call_count == 1  # 캐시된 토큰 재사용 — 재요청 없음


@respx.mock
def test_oauth2_refreshes_before_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """expires_in=100, buffer=60 → 만료 임계는 t=40. t=40에서는 아직 재발급 없이
    캐시된 토큰을 쓰고, t=41에서 headers()가 재발급을 트리거해야 한다.
    """
    monkeypatch.setenv("OAUTH_ID", "cid")
    monkeypatch.setenv("OAUTH_SECRET", "csecret")
    tokens = iter(["tok-1", "tok-2"])

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 100})

    route = respx.post("https://auth.test/token").mock(side_effect=responder)
    spec = AuthSpec(
        type="oauth2_client_credentials",
        token_url="https://auth.test/token",
        client_id_env="OAUTH_ID",
        client_secret_env="OAUTH_SECRET",
        expiry_buffer_s=60,
    )
    clock: Clock = FakeClock()
    auth = OAuth2ClientCredentials(spec, client=httpx.Client(), clock=clock)

    auth.headers()  # t=0: 최초 발급 → tok-1, expiry=100
    assert route.call_count == 1

    clock.now = 40.0  # threshold = expiry(100) - buffer(60) = 40. 40은 초과가 아니므로 재발급 없음.
    auth.headers()
    assert route.call_count == 1

    clock.now = 41.0  # 40 초과 → 재발급 트리거
    headers = auth.headers()
    assert headers == {"Authorization": "Bearer tok-2"}
    assert route.call_count == 2


@respx.mock
def test_oauth2_token_endpoint_error_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTH_ID", "cid")
    monkeypatch.setenv("OAUTH_SECRET", "csecret")
    respx.post("https://auth.test/token").respond(status_code=401, json={"error": "bad_client"})
    spec = AuthSpec(
        type="oauth2_client_credentials",
        token_url="https://auth.test/token",
        client_id_env="OAUTH_ID",
        client_secret_env="OAUTH_SECRET",
    )
    auth = OAuth2ClientCredentials(spec, client=httpx.Client())
    with pytest.raises(FatalError):
        auth.headers()


def test_build_auth_none_spec_returns_none() -> None:
    assert build_auth(None, httpx.Client()) is None


def test_build_auth_static_token() -> None:
    spec = AuthSpec(type="static_token", token_env="SOME_TOKEN")
    auth = build_auth(spec, httpx.Client())
    assert isinstance(auth, StaticTokenAuth)


def test_build_auth_oauth2() -> None:
    spec = AuthSpec(
        type="oauth2_client_credentials",
        token_url="https://auth.test/token",
        client_id_env="OAUTH_ID",
        client_secret_env="OAUTH_SECRET",
    )
    auth = build_auth(spec, httpx.Client())
    assert isinstance(auth, OAuth2ClientCredentials)


# ---------------------------------------------------------------------------
# D3: runner refresh loop — 401 → refresh() → 같은 unit 재시도.
#
# FlippingProvider는 refresh() 호출 전엔 "old" 토큰을, 호출 후엔 "new" 토큰을
# 돌려주는 이중 스텁이다. 서버는 "Bearer old"엔 401을, "Bearer new"엔 200을
# 응답한다 — provider.refresh()가 정확히 한 번 호출돼야 unit이 성공한다.
# ---------------------------------------------------------------------------


class FlippingProvider:
    def __init__(self) -> None:
        self._token = "old"
        self.refresh_count = 0

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def refresh(self) -> None:
        self.refresh_count += 1
        self._token = "new"


def _patch_build_auth(monkeypatch: pytest.MonkeyPatch, provider: "FlippingProvider") -> None:
    """pugio.runner.build_auth를 provider를 그대로 돌려주는 스텁으로 교체한다.

    lambda로 하면 pyright strict 모드에서 파라미터 타입을 추론 못 해 실패하므로
    build_auth와 동일한 시그니처를 명시한 top-level 함수로 대신한다.
    """

    def stub(
        spec: AuthSpec | None, client: httpx.Client, *, clock: Clock | None = None
    ) -> "FlippingProvider":
        return provider

    monkeypatch.setattr("pugio.runner.build_auth", stub)


def _auth_test_spec(tmp_path: Path, url: str) -> PipelineSpec:
    return PipelineSpec(
        name="auth-t",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url=url,
            pagination=PaginationSpec(mode="offset", size=2),
            # build_auth를 monkeypatch로 대체하므로 값 자체는 쓰이지 않는다 —
            # AuthSpec이 필수 필드이므로 유효한 인스턴스만 채워둔다.
            auth=AuthSpec(type="static_token", token_env="UNUSED"),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


@respx.mock
def test_401_triggers_refresh_and_same_unit_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FlippingProvider()
    _patch_build_auth(monkeypatch, provider)

    def responder(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == "Bearer old":
            return httpx.Response(401)
        return httpx.Response(200, json=[{"id": 1}])

    respx.get("https://api.test/auth-refresh-items").mock(side_effect=responder)

    report = run_pipeline(_auth_test_spec(tmp_path, "https://api.test/auth-refresh-items"))

    assert report.written == 1
    assert provider.refresh_count == 1


@respx.mock
def test_401_not_blindly_retried_before_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRITICAL — retry.py의 AuthExpiredError 제외가 실제로 동작함을 증명한다.

    max_attempts=5인 with_retry가 만약 AuthExpiredError를 여전히 재시도 대상으로
    보고 있었다면, refresh() 없이 "old" 토큰으로 서버를 최대 5번 두들겼을 것이다.
    수정 후에는 정확히 1번만 "old"로 맞고 즉시 AuthExpiredError가 escape → 러너가
    refresh() 후 "new"로 재요청한다.
    """
    provider = FlippingProvider()
    _patch_build_auth(monkeypatch, provider)
    old_token_calls = 0

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal old_token_calls
        if request.headers.get("authorization") == "Bearer old":
            old_token_calls += 1
            return httpx.Response(401)
        return httpx.Response(200, json=[{"id": 1}])

    respx.get("https://api.test/auth-no-blind-retry-items").mock(side_effect=responder)

    report = run_pipeline(
        _auth_test_spec(tmp_path, "https://api.test/auth-no-blind-retry-items"),
        max_attempts=5,
    )

    assert old_token_calls == 1  # blind 재시도였다면 5였을 것
    assert report.written == 1


@respx.mock
def test_transient_5xx_still_retried(tmp_path: Path) -> None:
    """backward-compat: AuthExpiredError만 제외됐을 뿐, 순수 RetryableError(5xx)는
    여전히 with_retry가 정상적으로 재시도해야 한다."""
    call_count = 0

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[{"id": 1}])

    respx.get("https://api.test/transient-5xx-items").mock(side_effect=responder)

    spec = PipelineSpec(
        name="transient-t",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url="https://api.test/transient-5xx-items",
            pagination=PaginationSpec(mode="offset", size=2),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )
    report = run_pipeline(spec, max_attempts=5)

    assert call_count == 3
    assert report.written == 1
