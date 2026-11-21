import json

import httpx
import pytest

from model_forensic.application.ports import ChatMessage, ToolSpec
from model_forensic.infrastructure.llm.openai_compatible import LLMSettings, OpenAICompatibleProvider
from model_forensic.shared.errors import PermissionDeniedError
from model_forensic.shared.retry import RetryPolicy


def provider(handler, monkeypatch, **kw) -> OpenAICompatibleProvider:
    monkeypatch.setenv("TEST_KEY", "sk-testkey-1234567890")
    s = LLMSettings(
        base_url="http://llm.local/v1",
        model="m",
        api_key_env="TEST_KEY",
        retry=RetryPolicy(max_attempts=3, base_delay_s=0),
        **kw,
    )
    return OpenAICompatibleProvider(s, client=httpx.Client(transport=httpx.MockTransport(handler)))


def ok(content=None, tool_calls=None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "m",
            "choices": [{"message": {"content": content, "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def test_request_shape_and_auth(monkeypatch) -> None:
    seen = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = (
            str(req.url),
            req.headers.get("authorization"),
            json.loads(req.content),
        )
        return ok("{}")

    r = provider(h, monkeypatch).complete([ChatMessage("user", "hi")], [ToolSpec("t", "d", {"type": "object"})])
    assert seen["url"] == "http://llm.local/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-testkey-1234567890"
    assert seen["body"]["tools"][0]["function"]["name"] == "t"
    assert r.prompt_tokens == 10


def test_azure_openai_mode(monkeypatch) -> None:
    seen = {}

    def h(req):
        seen["url"], seen["key"] = str(req.url), req.headers.get("api-key")
        return ok("x")

    provider(h, monkeypatch, azure_api_version="2024-06-01").complete([ChatMessage("user", "hi")])
    assert seen["url"].endswith("?api-version=2024-06-01") and seen["key"] == "sk-testkey-1234567890"


def test_retries_on_429_then_succeeds(monkeypatch) -> None:
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else ok("done")

    assert provider(h, monkeypatch).complete([ChatMessage("user", "hi")]).content == "done"
    assert len(calls) == 3


def test_auth_failure_is_not_retried(monkeypatch) -> None:
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(401)

    with pytest.raises(PermissionDeniedError):
        provider(h, monkeypatch).complete([ChatMessage("user", "hi")])
    assert len(calls) == 1
