"""OpenAI-compatible chat completions adapter (OpenAI, Azure OpenAI, vLLM, Ollama, LiteLLM, ...).

BYOK: the API key is read from an environment variable named in configuration (never stored in
config files, never logged). Supports tools, JSON mode, timeouts and bounded retries.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

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
    max_concurrency: int = 2  # bounded: requests beyond this wait their turn


class OpenAICompatibleProvider:
    def __init__(self, settings: LLMSettings, client: httpx.Client | None = None, api_key: str | None = None) -> None:
        self.settings = settings
        self._slots = threading.BoundedSemaphore(max(1, settings.max_concurrency))
        self.model = settings.model
        self._key = api_key or os.environ.get(settings.api_key_env)
        register_secret(self._key)
        # A local server (llama.cpp, LM Studio, Ollama, vLLM on 127.0.0.1) must never go through a corporate
        # HTTP(S)_PROXY from the environment — the usual reason "the model is detected but calls fail" in a container.
        self._client = client or httpx.Client(timeout=settings.timeout_s, trust_env=not is_loopback(settings.base_url))
        # Capabilities learnt from the server's answers (local servers often lack JSON mode or tool calling).
        self.no_json_mode = False
        self.no_tools = False

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def _url(self) -> str:
        base = normalize_base_url(self.settings.base_url)
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
        """One chat completion; at most ``max_concurrency`` run at once (the rest wait — never unbounded)."""
        with self._slots:
            return self._complete(messages, tools, json_mode=json_mode)

    def _complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] | None = None, *, json_mode: bool = False
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [_msg(m) for m in messages],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if tools and not self.no_tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        if json_mode and "tools" not in body and not self.no_json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.settings.reasoning_effort:
            body["reasoning_effort"] = self.settings.reasoning_effort

        def call() -> httpx.Response:
            try:
                r = self._client.post(self._url(), json=body, headers=self._headers())
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise BackendUnavailableError(
                    f"LLM transport error: {type(exc).__name__} — is the server running at {self.settings.base_url}?"
                ) from exc
            if r.status_code in (429, 502, 503, 504):
                raise BackendUnavailableError(f"LLM server returned {r.status_code}")
            if r.status_code in (401, 403):
                raise PermissionDeniedError(
                    f"LLM provider rejected credentials ({r.status_code}); check {self.settings.api_key_env}"
                )
            if r.status_code >= 400:
                raise ValidationError(f"LLM request failed ({r.status_code}): {r.text[:500]}")
            return r

        def call_adapting() -> httpx.Response:
            """Retry once without JSON mode / tools when the server says it does not support them."""
            for _ in range(3):
                try:
                    return call()
                except ValidationError as exc:
                    text = str(exc).lower()
                    if "context" in text and any(w in text for w in ("length", "size", "exceed", "window", "too long")):
                        raise ValidationError(
                            "The prompt is larger than the model's context window. Lower llm.context_limit_tokens "
                            "(e.g. 8000) in Settings → LLM, or start the server with a larger context (-c)."
                        ) from exc
                    if "tools" in body and any(w in text for w in ("tool", "jinja", "function")):
                        self.no_tools = True
                        body.pop("tools", None)
                        if json_mode:
                            body["response_format"] = {"type": "json_object"}
                        log.warning("llm server rejects tool calling; continuing without tools")
                        continue
                    if "response_format" in body and any(w in text for w in ("response_format", "json", "grammar")):
                        self.no_json_mode = True
                        body.pop("response_format", None)
                        log.warning("llm server rejects JSON mode; continuing without it")
                        continue
                    raise
            return call()

        t0 = time.perf_counter()
        resp = retry(call_adapting, self.settings.retry)
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
        content = choice.get("content")
        if isinstance(content, str) and "<think>" in content:  # reasoning models: keep only the answer
            content = _THINK.sub("", content).strip()
        return LLMResponse(
            content=content,
            tool_calls=choice.get("tool_calls") or [],
            model=data.get("model", self.settings.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=latency,
        )


_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)


def normalize_base_url(url: str) -> str:
    """'127.0.0.1:8080/v1' → 'http://127.0.0.1:8080/v1' (local servers speak plain HTTP); trailing '/' and a pasted
    '/chat/completions' are removed."""
    u = url.strip()
    if "://" not in u:
        u = ("http://" if is_loopback("http://" + u) else "https://") + u
    u = u.rstrip("/")
    return u[: -len("/chat/completions")] if u.endswith("/chat/completions") else u


def is_loopback(url: str) -> bool:
    host = (urlparse(url if "://" in url else "http://" + url).hostname or "").lower()
    return host in {"localhost", "::1", "host.docker.internal"} or host.startswith("127.")


def _msg(m: ChatMessage) -> dict[str, Any]:
    d: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_call_id:
        d["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        d["tool_calls"] = m.tool_calls
    if m.name:
        d["name"] = m.name
    return d
