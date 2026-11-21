"""OpenAI-compatible chat completions adapter (OpenAI, Azure OpenAI, vLLM, Ollama, LiteLLM, ...).

BYOK: the API key is read from an environment variable named in configuration (never stored in
config files, never logged). Supports tools, JSON mode, timeouts and bounded retries.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from model_forensic.application.ports import ChatMessage, LLMResponse, ToolSpec
from model_forensic.shared.errors import BackendUnavailableError, PermissionDeniedError, ValidationError
from model_forensic.shared.logging import get_logger, register_secret
from model_forensic.shared.retry import RetryPolicy, retry

log = get_logger(__name__)


@dataclass(frozen=True)
class LLMSettings:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "MF_LLM_API_KEY"
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout_s: float = 120.0
    context_limit_tokens: int = 64_000
    reasoning_effort: str | None = None  # passed through where the server supports it
    extra_headers: dict[str, str] = field(default_factory=dict)
    azure_api_version: str | None = None  # set for Azure OpenAI deployments
    retry: RetryPolicy = field(default_factory=lambda: RetryPolicy(max_attempts=3))


class OpenAICompatibleProvider:
    def __init__(self, settings: LLMSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.model = settings.model
        self._key = os.environ.get(settings.api_key_env)
        register_secret(self._key)
        self._client = client or httpx.Client(timeout=settings.timeout_s)

    def _url(self) -> str:
        base = self.settings.base_url.rstrip("/")
        url = f"{base}/chat/completions"
        if self.settings.azure_api_version:
            url += f"?api-version={self.settings.azure_api_version}"
        return url

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", **self.settings.extra_headers}
        if self._key:
            if self.settings.azure_api_version:
                h["api-key"] = self._key
            else:
                h["Authorization"] = f"Bearer {self._key}"
        return h

    def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] | None = None, *, json_mode: bool = False
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [_msg(m) for m in messages],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        if json_mode and not tools:
            body["response_format"] = {"type": "json_object"}
        if self.settings.reasoning_effort:
            body["reasoning_effort"] = self.settings.reasoning_effort

        def call() -> httpx.Response:
            try:
                r = self._client.post(self._url(), json=body, headers=self._headers())
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise BackendUnavailableError(f"LLM transport error: {type(exc).__name__}") from exc
            if r.status_code in (429, 500, 502, 503, 504):
                raise BackendUnavailableError(f"LLM server returned {r.status_code}")
            if r.status_code in (401, 403):
                raise PermissionDeniedError(
                    f"LLM provider rejected credentials ({r.status_code}); check {self.settings.api_key_env}"
                )
            if r.status_code >= 400:
                raise ValidationError(f"LLM request failed ({r.status_code}): {r.text[:300]}")
            return r

        t0 = time.perf_counter()
        resp = retry(call, self.settings.retry)
        latency = (time.perf_counter() - t0) * 1000
        data = resp.json()
        choice = data["choices"][0]["message"]
        usage = data.get("usage") or {}
        log.info(
            "llm call",
            model=self.settings.model,
            latency_ms=round(latency, 1),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
        return LLMResponse(
            content=choice.get("content"),
            tool_calls=choice.get("tool_calls") or [],
            model=data.get("model", self.settings.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=latency,
        )


def _msg(m: ChatMessage) -> dict[str, Any]:
    d: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_call_id:
        d["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        d["tool_calls"] = m.tool_calls
    if m.name:
        d["name"] = m.name
    return d
