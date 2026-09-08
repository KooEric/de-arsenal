"""LLM 제공자 — SDK 없이 httpx. 에러는 arsenal_core 분류 체계로 변환한다.

429/5xx → RetryableError(with_retry가 backoff), 401 → AuthExpiredError, 그 외 4xx →
FatalError. 비밀은 환경변수로만(ANTHROPIC_API_KEY / OPENAI_API_KEY). 로그에 키 출력 금지.
FakeProvider는 테스트·eval 재현용 — 질문→응답 사전을 그대로 돌려준다.
"""

import os
import typing as t
from dataclasses import dataclass

import httpx

from arsenal_core.errors import FatalError, RetryableError, classify_http_status
from arsenal_core.retry import with_retry

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 1024
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


class Provider(t.Protocol):
    @property
    def name(self) -> str: ...

    def complete(self, system: str, user: str) -> Completion: ...


def _raise_for_status(resp: httpx.Response) -> None:
    err = classify_http_status(resp.status_code)
    if err is None:
        return
    body = resp.text[:300]
    raise err(f"{resp.request.method} {resp.request.url.host}: {resp.status_code} {body}")


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise FatalError(f"{key} is not set")
    return val


@dataclass(frozen=True)
class AnthropicProvider:
    model: str = "claude-sonnet-4-6"
    name: str = "anthropic"

    def complete(self, system: str, user: str) -> Completion:
        def call() -> Completion:
            try:
                resp = httpx.post(
                    ANTHROPIC_URL,
                    headers={
                        "x-api-key": _env("ANTHROPIC_API_KEY"),
                        "anthropic-version": ANTHROPIC_VERSION,
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": DEFAULT_MAX_TOKENS,
                        "temperature": 0,
                        "system": system,
                        "messages": [{"role": "user", "content": user}],
                    },
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
            except httpx.TransportError as e:
                raise RetryableError(f"anthropic transport error: {e}") from e
            _raise_for_status(resp)
            data = t.cast(dict[str, t.Any], resp.json())
            text = "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
            usage = data.get("usage", {})
            return Completion(
                text,
                str(data.get("model", self.model)),
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
            )

        return with_retry(call)


@dataclass(frozen=True)
class OpenAIProvider:
    model: str = "gpt-4o-mini"
    name: str = "openai"

    def complete(self, system: str, user: str) -> Completion:
        def call() -> Completion:
            try:
                resp = httpx.post(
                    OPENAI_URL,
                    headers={"authorization": f"Bearer {_env('OPENAI_API_KEY')}"},
                    json={
                        "model": self.model,
                        "temperature": 0,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    },
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
            except httpx.TransportError as e:
                raise RetryableError(f"openai transport error: {e}") from e
            _raise_for_status(resp)
            data = t.cast(dict[str, t.Any], resp.json())
            text = str(data["choices"][0]["message"]["content"])
            usage = data.get("usage", {})
            return Completion(
                text,
                str(data.get("model", self.model)),
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )

        return with_retry(call)


@dataclass(frozen=True)
class FakeProvider:
    """user 프롬프트에 포함된 키(질문 문자열)로 응답을 고른다. 없으면 default."""

    responses: dict[str, str]
    default: str = "SELECT 1"
    name: str = "fake"

    def complete(self, system: str, user: str) -> Completion:
        for key, text in self.responses.items():
            if key in user:
                return Completion(text, "fake", len(user) // 4, len(text) // 4)
        return Completion(self.default, "fake", len(user) // 4, 1)


def provider_from_name(name: str, model: str | None = None) -> Provider:
    if name == "anthropic":
        return AnthropicProvider(model=model or AnthropicProvider.model)
    if name == "openai":
        return OpenAIProvider(model=model or OpenAIProvider.model)
    raise FatalError(f"unknown provider: {name!r} (anthropic | openai)")
