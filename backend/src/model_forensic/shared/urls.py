"""URL helpers for LLM endpoints (pure; used by the LLM adapter and the connection use case)."""

from __future__ import annotations

from urllib.parse import urlparse


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
