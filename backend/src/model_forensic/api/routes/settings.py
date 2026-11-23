"""HTTP routes: /backends, /check, /facets, /glossary, /me, /scheduler, /settings."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException

from model_forensic.api.common import Job, diagnosis_for
from model_forensic.api.routes.context import RouteContext
from model_forensic.application.ports import ListRunsQuery
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.shared import identity


def register(app: FastAPI, ctx: RouteContext) -> None:
    _knowledge_for = ctx._knowledge_for
    _pending = ctx._pending
    container: Container = ctx.container
    jobs = ctx.jobs
    p = ctx.p
    server = ctx.server
    svc: ForensicService = ctx.svc

    @app.get(f"{p}/backends")
    def backends() -> list[dict[str, Any]]:
        """Configured workspaces for the workspace selector (no secrets: Azure uses DefaultAzureCredential)."""
        out = []
        for b in container.settings.backends:
            o = b.options
            out.append(
                {
                    "name": b.name,
                    "kind": b.kind,
                    "title": b.title or b.name,
                    "workspace": o.get("workspace"),
                    "resource_group": o.get("resource_group"),
                    "subscription_id": o.get("subscription_id"),
                    "root": o.get("root") if b.kind == "local" else None,
                    "available": b.name in svc.sources and b.name not in svc.sync_errors,
                    "verified": b.name in svc.sync_ok,
                    "error": container.backend_errors.get(b.name) or svc.sync_errors.get(b.name),
                    "diagnosis": diagnosis_for(container.backend_errors.get(b.name) or svc.sync_errors.get(b.name)),
                }
            )
        return out

    @app.post(f"{p}/backends/{{name}}/check")
    def backend_check(name: str, run: str | None = None) -> dict[str, str]:
        """Live connection test of one workspace (the same steps as ``mf doctor --live``), as a job."""
        if name not in {b.name for b in container.settings.backends}:
            raise HTTPException(404, f"no backend named {name!r}")
        job = Job(id=uuid.uuid4().hex[:10], kind="check", title=f"Connection test {name}")
        jobs[job.id] = job

        def work() -> None:
            from model_forensic.infrastructure.connectors.azureml.diagnose import classify
            from model_forensic.infrastructure.connectors.azureml.live import run_checks

            try:
                src = svc.sources.get(name)
                if src is None:
                    err = container.backend_errors.get(name) or "backend could not be created"
                    job.result = {
                        "ok": False,
                        "checks": [
                            {"name": "connect", "ok": False, "detail": err, "diagnosis": classify(err).to_dict()}
                        ],
                    }
                elif not hasattr(src, "_raw_metric_names"):
                    t0 = time.perf_counter()
                    page = src.list_runs(ListRunsQuery(page_size=10))
                    job.result = {
                        "ok": True,
                        "checks": [
                            {
                                "name": "list_runs",
                                "ok": True,
                                "seconds": time.perf_counter() - t0,
                                "detail": f"{len(page.items)} runs on the first page",
                            }
                        ],
                    }
                else:
                    checks = run_checks(src, run)
                    failed = next((c for c in checks if not c.ok), None)
                    if failed is None:
                        svc.sync_errors.pop(name, None)
                        svc.sync_ok.add(name)
                    else:
                        svc.sync_errors[name] = failed.detail
                    job.result = {
                        "ok": all(c.ok for c in checks),
                        "checks": [
                            {
                                "name": c.name,
                                "ok": c.ok,
                                "seconds": c.seconds,
                                "slow": c.slow,
                                "detail": c.detail,
                                **({"diagnosis": classify(c.detail).to_dict()} if not c.ok else {}),
                            }
                            for c in checks
                        ],
                    }
                job.status = "done"
            except Exception as exc:
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"

        threading.Thread(target=work, daemon=True).start()
        return {"job_id": job.id}

    @app.get(f"{p}/scheduler")
    def scheduler_status() -> dict[str, Any]:
        sch = app.state.scheduler
        return {
            "enabled": server.mode == "shared" and server.sync_minutes > 0,
            "sync_minutes": server.sync_minutes,
            "last_sync": sch.last_sync.isoformat() if sch.last_sync else None,
            "history": sch.history[-10:],
            "webhook_configured": sch.notifier.url is not None,
            "nightly_books_utc": server.nightly_books_utc,
        }

    @app.get(f"{p}/me")
    def me() -> dict[str, Any]:
        u = identity.current()
        return {
            "id": u.id,
            "name": u.name,
            "admin": u.admin,
            "mode": container.settings.server.mode,
            "run_names": svc.me(),
        }

    @app.get(f"{p}/check")
    def check(run: str | None = None) -> dict[str, Any]:
        """Beginner "Check my run": at most five plain-language cards (newest completed run by default)."""
        from model_forensic.application.check_run import check_run

        try:
            res = check_run(svc, run, knowledge=_knowledge_for)
            from model_forensic.infrastructure.artifacts.store import DownloadProfile

            ref = (res.get("run") or {}).get("ref")
            if ref:  # evaluation checks read what is downloaded; say what is not
                res["pending"] = [_pending(str(ref), DownloadProfile.EVAL_JSON)]
            return res
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get(f"{p}/glossary")
    def glossary() -> dict[str, str]:
        from model_forensic.application.check_run import GLOSSARY

        return GLOSSARY

    @app.get(f"{p}/facets")
    def facets_config(run: str | None = None) -> dict[str, Any]:
        """Filter facets (defaults + config/facets.local.yaml); with ``run``, what that run's names carry."""
        from model_forensic.application import facets as fc

        cfg = fc.load()
        out: dict[str, Any] = cfg.model_dump()
        if run:
            out["observed"] = fc.observed([str(s.key) for s in svc.series(run)], cfg)
        return out

    @app.get(f"{p}/settings/thresholds")
    def settings_thresholds() -> dict[str, Any]:
        """Thresholds of the analyses (defaults/analysis.yaml, overridden in team.local.yaml → analysis_params)."""
        from model_forensic.analysis import thresholds as th

        return {"thresholds": th.table()}

    @app.get(f"{p}/settings/team")
    def team_settings() -> dict[str, Any]:
        """Team policy (config/team.local.yaml): de-prioritised classes, expected metrics, code refs, defaults."""
        from model_forensic.interface import team

        return {**svc.team.model_dump(), "path": str(team.path())}

    @app.get(f"{p}/me/names")
    def me_get() -> dict[str, Any]:
        """The names that mean "me" in runs (exact, case-insensitive match — never a guess from other text)."""
        u = identity.current()
        if u is not identity.LOCAL:
            return {"names": [x for x in (u.name, u.id) if x], "source": "signed in to this server", "editable": False}
        if container.settings.me:
            return {
                "names": list(container.settings.me),
                "source": "config/azure.local.yaml (defaults.me)",
                "editable": True,
            }
        try:
            names = svc.me()
        except Exception:  # no az login: nothing is assumed
            names = []
        return {"names": names, "source": "az login (signed-in user)" if names else "not set", "editable": True}

    @app.put(f"{p}/me/names")
    def me_put(body: dict[str, Any]) -> dict[str, Any]:
        """Set the names that mean "me" (add / remove happen here; also from a run page: *Add to Me*)."""
        from model_forensic.interface import azure

        if identity.current() is not identity.LOCAL:
            raise HTTPException(403, "on a shared server, 'me' is whoever is signed in")
        names = [str(x) for x in body.get("names") or []]
        try:
            azure.set_me(names)
        except azure.InterfaceError as exc:
            raise HTTPException(422, str(exc)) from exc
        container.settings.me = list(dict.fromkeys(n.strip() for n in names if n.strip()))
        container.docs.audit("local", "me.set", {"count": len(container.settings.me)})
        return me_get()

    @app.get(f"{p}/settings/azure")
    def azure_settings() -> dict[str, Any]:
        """The local Azure interface (config/azure.local.yaml or MF_AZURE_* env). It holds no secrets."""
        from model_forensic.interface import azure

        try:
            view = azure.load().public_view()
        except azure.InterfaceError as exc:
            return {
                "source": None,
                "workspaces": [],
                "defaults": {},
                "error": str(exc),
                "path": str(azure.interface_path()),
            }
        return {**view, "path": str(azure.interface_path()), "notices": container.settings.notices}

    @app.post(f"{p}/settings/azure/detect")
    def azure_detect(subscription_id: str | None = None) -> dict[str, Any]:
        """Ask the local `az` CLI for the active subscription, its ML workspaces and the signed-in user."""
        from model_forensic.interface import azure

        try:
            return azure.detect(subscription_id)
        except azure.InterfaceError as exc:
            raise HTTPException(424, str(exc)) from exc

    @app.put(f"{p}/settings/azure")
    def azure_save(body: dict[str, Any]) -> dict[str, Any]:
        """Write config/azure.local.yaml (git-ignored) and reconnect the Azure workspaces."""
        from model_forensic.interface import azure

        try:
            iface = azure.AzureInterface.model_validate(
                {k: body.get(k) or ({} if k == "defaults" else []) for k in ("defaults", "workspaces")}
            )
            names = [w.name for w in iface.workspaces]
            if len(names) != len(set(names)):
                raise ValueError("workspace names must be unique")
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc
        path = azure.save(iface)
        container.reload_azure_interface()
        container.docs.audit("api", "settings.azure", {"path": str(path), "workspaces": len(iface.workspaces)})
        return azure_settings()
