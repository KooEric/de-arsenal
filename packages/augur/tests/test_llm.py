"""프로바이더 HTTP 계층 — 응답 파싱은 공식 스키마 기준, 에러는 arsenal_core 분류로."""

import json
import typing as t

import httpx
import pytest
import respx
from httpx import Response

from arsenal_core.errors import AuthExpiredError, FatalError
from augur.llm import (
    ANTHROPIC_URL,
    OPENAI_URL,
    AnthropicProvider,
    OpenAIProvider,
    provider_from_name,
)

ANTHROPIC_OK = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "SELECT 1"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 12, "output_tokens": 3},
}
OPENAI_OK = {
    "id": "chatcmpl-1",
    "model": "gpt-4o-mini-2024-07-18",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "SELECT 2"}}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 4},
}


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")


@respx.mock
def test_anthropic_parses_messages_response(keys: None) -> None:
    route = respx.post(ANTHROPIC_URL).mock(return_value=Response(200, json=ANTHROPIC_OK))
    c = AnthropicProvider().complete("sys", "user")
    assert (c.text, c.model) == ("SELECT 1", "claude-sonnet-4-6")
    assert (c.input_tokens, c.output_tokens) == (12, 3)
    req = t.cast(httpx.Request, route.calls[0].request)  # pyright: ignore[reportUnknownMemberType]
    body = json.loads(req.content)
    assert req.headers["x-api-key"] == "test-anthropic-key"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "user"}]


@respx.mock
def test_openai_parses_chat_completion(keys: None) -> None:
    route = respx.post(OPENAI_URL).mock(return_value=Response(200, json=OPENAI_OK))
    c = OpenAIProvider().complete("sys", "user")
    assert (c.text, c.input_tokens, c.output_tokens) == ("SELECT 2", 20, 4)
    req = t.cast(httpx.Request, route.calls[0].request)  # pyright: ignore[reportUnknownMemberType]
    assert req.headers["authorization"] == "Bearer test-openai-key"
    assert json.loads(req.content)["messages"][0] == {"role": "system", "content": "sys"}


@respx.mock
def test_4xx_is_fatal_and_not_retried(keys: None) -> None:
    route = respx.post(ANTHROPIC_URL).mock(
        return_value=Response(400, json={"error": {"message": "bad request"}})
    )
    with pytest.raises(FatalError, match="400"):
        AnthropicProvider().complete("s", "u")
    assert route.call_count == 1


@respx.mock
def test_401_raises_auth_expired_without_retry(keys: None) -> None:
    route = respx.post(OPENAI_URL).mock(return_value=Response(401, text="unauthorized"))
    with pytest.raises(AuthExpiredError):
        OpenAIProvider().complete("s", "u")
    assert route.call_count == 1


def test_missing_api_key_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(FatalError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider().complete("s", "u")


def test_provider_from_name() -> None:
    assert provider_from_name("anthropic", "m").name == "anthropic"
    assert provider_from_name("openai").name == "openai"
    with pytest.raises(FatalError, match="unknown provider"):
        provider_from_name("gemini")
