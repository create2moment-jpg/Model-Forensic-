"""Connect to the local LLM server in one click, and remember what works.

* ``ping``: one short chat with the active model — the status the header shows (ok / failed / offline).
* ``connect_local``: ask the local OpenAI-compatible server (its address is configured or given once — never assumed)
  which model it serves
  (``GET /models``), send one chat, and only if the reply comes back switch the app to it.
* The LLM settings (never a key) are saved in ``<data_dir>/llm.json`` and restored at start, so a working
  connection survives a restart; ``model-forensic.yaml`` stays the default until the UI changes it.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from model_forensic.application.ports import ChatMessage, is_offline
from model_forensic.settings import LLMConfig
from model_forensic.shared.urls import is_loopback, normalize_base_url

FILE = "llm.json"


def save(data_dir: Path, cfg: LLMConfig) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / (FILE + ".tmp")
    tmp.write_text(json.dumps(cfg.model_dump(), indent=2), encoding="utf-8")
    tmp.replace(data_dir / FILE)


def load(data_dir: Path) -> LLMConfig | None:
    p = data_dir / FILE
    if not p.is_file():
        return None
    try:
        return LLMConfig.model_validate(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None  # an unreadable file falls back to model-forensic.yaml


def discover_models(base_url: str, timeout_s: float = 5.0, client: httpx.Client | None = None) -> list[str]:
    """Model ids an OpenAI-compatible server reports at ``/models`` (llama.cpp, vLLM, Ollama, LM Studio…)."""
    c = client or httpx.Client(timeout=timeout_s, trust_env=not is_loopback(base_url))
    r = c.get(base_url.rstrip("/") + "/models")
    r.raise_for_status()
    body = r.json()
    items = body.get("data") if isinstance(body, dict) else body
    return [str(m.get("id")) for m in items or [] if isinstance(m, dict) and m.get("id")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ping(container: Any) -> dict[str, Any]:
    """One short chat with the active model; the result is kept as the status the header shows."""

    if is_offline(container.llm):
        st = {"ok": False, "state": "offline", "model": container.llm.model, "at": _now(), "error": None}
    else:
        t0 = time.perf_counter()
        try:
            r = container.llm.complete([ChatMessage("user", "Reply with the single word OK.")])
            st = {
                "ok": True,
                "state": "ok",
                "model": r.model or container.llm.model,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "at": _now(),
                "error": None,
            }
        except Exception as exc:
            st = {
                "ok": False,
                "state": "failed",
                "model": container.llm.model,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "at": _now(),
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
    container.llm_status = st
    return st


def connect_local(
    container: Any, base_url: str | None = None, model: str | None = None, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Find the model on a local OpenAI-compatible server, test it, and switch to it only if it answers."""
    given = base_url or getattr(container.settings, "local_llm_url", "")
    if not given:  # never assume an address: ask once, then it is remembered
        return {
            "ok": False,
            "needs_url": True,
            "error": "Which address does your local LLM server listen on (e.g. host:port/v1)? It is remembered after "
            "the first successful connection; MF_LOCAL_LLM_URL or local_llm_url in model-forensic.yaml set it too.",
        }
    url = normalize_base_url(given)
    try:
        models = discover_models(url, client=client)
    except Exception as exc:
        return {
            "ok": False,
            "base_url": url,
            "error": f"No server answers at {url}/models ({type(exc).__name__}). Start it — e.g. llama.cpp: "
            "llama-server -m <model>.gguf --port 8080 --jinja — then try again.",
        }
    chosen = model or (models[0] if models else container.settings.llm.model)
    cfg = container.settings.llm.model_copy(
        update={"enabled": True, "base_url": url, "model": chosen, "local_url": url}
    )
    llm = container.make_llm(cfg, container.session_api_key)
    t0 = time.perf_counter()
    try:
        r = llm.complete([ChatMessage("user", "Reply with the single word OK.")])
    except Exception as exc:
        return {
            "ok": False,
            "base_url": url,
            "models": models,
            "model": chosen,
            "error": f"The server lists {chosen!r} but the test chat failed: {type(exc).__name__}: {str(exc)[:300]}",
        }
    latency = round((time.perf_counter() - t0) * 1000)
    container.configure_llm(cfg)
    container.settings.local_llm_url = url
    save(container.settings.data_dir, cfg)
    container.llm_status = {
        "ok": True,
        "state": "ok",
        "model": chosen,
        "latency_ms": latency,
        "at": _now(),
        "error": None,
    }
    return {
        "ok": True,
        "base_url": url,
        "models": models,
        "model": chosen,
        "latency_ms": latency,
        "reply": (r.content or "")[:80],
    }
