"""HTTP routes: /investigations, /settings."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException

from model_forensic.api.common import LLMSettingsRequest
from model_forensic.api.routes.context import RouteContext
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.settings import LLMConfig
from model_forensic.shared import identity


def register(app: FastAPI, ctx: RouteContext) -> None:
    container: Container = ctx.container
    p = ctx.p
    svc: ForensicService = ctx.svc

    @app.get(f"{p}/settings/llm")
    def get_llm() -> dict[str, Any]:
        c = container.settings.llm
        env_key = bool(__import__("os").environ.get(c.api_key_env))
        return {
            **c.model_dump(),
            "has_session_key": bool(container.session_api_key),
            "has_env_key": env_key,
            "active_model": container.llm.model,
        }

    @app.put(f"{p}/settings/llm")
    def put_llm(req: LLMSettingsRequest) -> dict[str, Any]:
        from model_forensic.application import llm_connect

        data = req.model_dump(exclude={"api_key"})
        container.configure_llm(LLMConfig(**data), req.api_key)
        llm_connect.save(container.settings.data_dir, container.settings.llm)  # survives a restart (no key)
        container.llm_status = None
        if req.api_key:
            from model_forensic.shared.logging import register_secret

            register_secret(req.api_key)
        container.docs.audit(
            "api",
            "llm_settings",
            {
                "base_url": req.base_url,
                "model": req.model,
                "enabled": req.enabled,
                "key_changed": req.api_key is not None,
            },
        )
        return get_llm()

    @app.get(f"{p}/settings/storage")
    def storage_get() -> dict[str, Any]:
        """Every storage role: effective path, used / free space, device type, problems, what deleting it costs;
        the file guide; and the download-worker recommendation."""
        from model_forensic.application import storage

        return storage.describe(container.settings)

    @app.post(f"{p}/settings/storage/validate")
    def storage_validate(body: dict[str, Any]) -> dict[str, Any]:
        from model_forensic.application import storage

        path = str(body.get("path") or "").strip()
        if not path:
            raise HTTPException(422, "path is required")
        return storage.validate_path(path, str(body.get("role") or "artifacts"))

    @app.put(f"{p}/settings/storage")
    def storage_put(body: dict[str, Any]) -> dict[str, Any]:
        """Save path overrides (validated; applied at the next start) and the worker count (applied now)."""
        from model_forensic.application import storage

        updates: dict[str, Any] = {}
        for role in storage.ROLES:
            k = f"{role}_dir"
            if k in body:
                v = str(body[k] or "").strip()
                if v:
                    chk = storage.validate_path(v, role)
                    if not chk["ok"]:
                        raise HTTPException(422, f"{role}: {'; '.join(chk['problems'])}")
                updates[k] = v or None
        if "download_workers" in body:
            updates["download_workers"] = int(body["download_workers"])
        path = storage.save(updates)
        if "download_workers" in updates:
            container.settings.download_workers = updates["download_workers"]
            svc.artifact_store.workers = updates["download_workers"]  # new downloads use it right away
        container.docs.audit(identity.current().id, "settings.storage", {k: str(v) for k, v in updates.items()})
        applied: list[str] = []
        if "artifacts_dir" in updates:  # the download folder switches now unless a download is running
            downloading = any(j.kind == "download" and j.status == "running" for j in ctx.jobs.values()) or bool(
                getattr(container.prefetcher, "busy", lambda: False)()
            )
            if not downloading:
                from model_forensic.shared.paths import home_path

                container.settings.storage.artifacts_dir = (
                    home_path(updates["artifacts_dir"]) if updates["artifacts_dir"] else None
                )
                container.use_artifacts_dir(container.settings.paths()["artifacts"])
                applied.append("artifacts")
        restart = any(k.endswith("_dir") and k[: -len("_dir")] not in applied for k in updates)
        return {**storage.describe(container.settings), "saved_to": str(path), "restart_required": restart,
                "applied_now": applied}  # fmt: skip

    @app.post(f"{p}/settings/llm/ping")
    def ping_llm() -> dict[str, Any]:
        """One short chat with the active model — the status dot in the header."""
        from model_forensic.application import llm_connect

        return llm_connect.ping(container)

    @app.post(f"{p}/settings/llm/connect-local")
    def connect_local_llm(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Find the model on the local server (default 127.0.0.1:8080/v1), test it, switch to it only if it answers."""
        from model_forensic.application import llm_connect

        body = body or {}
        res = llm_connect.connect_local(container, body.get("base_url") or None, body.get("model") or None)
        container.docs.audit(
            identity.current().id, "llm.connect_local", {k: res.get(k) for k in ("ok", "base_url", "model")}
        )
        return res

    @app.post(f"{p}/settings/llm/test")
    def test_llm() -> dict[str, Any]:
        """Probe what the configured server can do: plain chat, JSON mode, tool calling — each reported separately,
        so a local server's missing feature shows up here instead of as a failing investigation."""
        from model_forensic.application.ports import ChatMessage, ToolSpec

        llm = container.llm
        checks: list[dict[str, Any]] = []

        def probe(name: str, fn: Any) -> Any:
            t0 = time.perf_counter()
            try:
                r = fn()
                checks.append({"name": name, "ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000)})
                return r
            except Exception as exc:
                checks.append(
                    {
                        "name": name,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                        "latency_ms": round((time.perf_counter() - t0) * 1000),
                    }
                )
                return None

        t_all = time.perf_counter()
        r = probe("chat", lambda: llm.complete([ChatMessage("user", "Reply with the single word OK.")]))
        if r is not None:
            probe(
                "json_mode", lambda: llm.complete([ChatMessage("user", 'Answer as JSON: {"ok": true}')], json_mode=True)
            )
            tool = ToolSpec(
                name="get_value",
                description="Returns a value.",
                parameters={"type": "object", "properties": {}, "required": []},
            )
            probe("tools", lambda: llm.complete([ChatMessage("user", "Call get_value.")], [tool]))
        notes = []
        if getattr(llm, "no_json_mode", False):
            notes.append("The server does not support JSON mode; answers are requested as plain text and parsed.")
        if getattr(llm, "no_tools", False):
            notes.append(
                "The server does not support tool calling (llama.cpp: start it with --jinja); the AI "
                "answers from the evidence it is given, without fetching more."
            )
        first_err = next((c["error"] for c in checks if not c["ok"]), None)
        return {
            "ok": bool(checks) and checks[0]["ok"],
            "model": (r.model if r is not None else getattr(llm, "model", "")),
            "latency_ms": round((time.perf_counter() - t_all) * 1000),
            "reply": ((r.content or "")[:200] if r is not None else None),
            "error": first_err if r is None else None,
            "checks": checks,
            "notes": notes,
        }

    @app.get(f"{p}/investigations")
    def investigations(run: str | None = None) -> list[dict[str, Any]]:
        if run:  # only this run's investigations are read, not all of them
            docs = [
                container.docs.get_raw("investigation", i)
                for i in container.docs.ids_for_runs("investigation", [svc.ref(run)])
            ]
            docs.sort(key=lambda d: str(d.get("created_at", "")), reverse=True)
        else:
            docs = container.docs.list_raw("investigation", limit=100)
        return [
            {
                k: d.get(k)
                for k in ("id", "question", "summary", "model", "created_at", "verified_ratio", "prompt", "persona")
            }
            for d in docs[:100]
        ]

    @app.get(f"{p}/investigations/{{inv_id}}")
    def investigation(inv_id: str) -> dict[str, Any]:
        return container.docs.get_raw("investigation", inv_id)
