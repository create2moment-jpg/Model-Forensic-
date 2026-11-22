"""Local OpenAI-compatible servers (llama.cpp, LM Studio, Ollama, vLLM): scheme-less URLs, no proxy for loopback,
JSON mode / tool calling rejected, <think> blocks, context overflow."""

import json

import httpx
import pytest

from model_forensic.application.ports import ChatMessage, ToolSpec
from model_forensic.infrastructure.llm.openai_compatible import (
    LLMSettings,
    OpenAICompatibleProvider,
    is_loopback,
    normalize_base_url,
)
from model_forensic.shared.errors import ValidationError


def strict_server(seen: list[dict]):  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append({"url": str(req.url), **body})
        if "tools" in body:
            return httpx.Response(500, text='{"error":"tools param requires --jinja flag"}')
        if "response_format" in body:
            return httpx.Response(400, text="'response_format.type' must be 'json_schema' or 'text'")
        if len(json.dumps(body["messages"])) > 5000:
            return httpx.Response(400, text="the request exceeds the available context size, try increasing it")
        content = '<think>reasoning about {braces}</think>{"summary": "fine", "claims": []}'
        return httpx.Response(200, json={"model": "qwen-local", "choices": [{"message": {"content": content}}]})

    return handler


def provider(seen: list[dict]) -> OpenAICompatibleProvider:
    client = httpx.Client(transport=httpx.MockTransport(strict_server(seen)))
    return OpenAICompatibleProvider(LLMSettings(base_url="127.0.0.1:8080/v1", model="qwen-local"), client=client)


def test_url_normalisation_and_loopback() -> None:
    assert normalize_base_url("127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1"
    assert normalize_base_url("http://localhost:8080/v1/chat/completions/") == "http://localhost:8080/v1"
    assert normalize_base_url("api.example.com/v1") == "https://api.example.com/v1"
    assert is_loopback("127.0.0.1:8080/v1") and is_loopback("http://localhost:1234") and not is_loopback("https://x.ai")


def test_loopback_bypasses_environment_proxy(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    p = OpenAICompatibleProvider(LLMSettings(base_url="http://127.0.0.1:8080/v1"))
    assert p._client._trust_env is False
    q = OpenAICompatibleProvider(LLMSettings(base_url="https://api.example.com/v1"))
    assert q._client._trust_env is True


def test_json_mode_and_tools_fall_back_and_think_is_stripped() -> None:
    seen: list[dict] = []
    p = provider(seen)
    r = p.complete([ChatMessage("user", "hi")], json_mode=True)
    assert r.content == '{"summary": "fine", "claims": []}'
    assert p.no_json_mode and seen[0]["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    tool = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
    r = p.complete([ChatMessage("user", "hi")], [tool], json_mode=False)
    assert p.no_tools and r.content
    n = len(seen)
    p.complete([ChatMessage("user", "again")], [tool], json_mode=True)
    assert len(seen) == n + 1  # learnt: no rejected attempts any more
    assert "tools" not in seen[-1] and "response_format" not in seen[-1]


def test_context_overflow_has_an_actionable_message() -> None:
    p = provider([])
    with pytest.raises(ValidationError, match="context window"):
        p.complete([ChatMessage("user", "x" * 6000)])


def test_settings_probe_reports_capabilities(container, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    seen: list[dict] = []
    container.llm = provider(seen)
    r = TestClient(create_app(container)).post("/api/v1/settings/llm/test").json()
    assert r["ok"] and [c["name"] for c in r["checks"]] == ["chat", "json_mode", "tools"]
    assert any("JSON mode" in n for n in r["notes"]) and any("--jinja" in n for n in r["notes"])
