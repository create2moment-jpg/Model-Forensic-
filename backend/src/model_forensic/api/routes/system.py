"""HTTP routes: /health, /jobs, /runs, /suggest, /sync."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from model_forensic import __version__
from model_forensic.api.common import DownloadRequest, Job, diagnosis_for
from model_forensic.api.routes.context import RouteContext
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.domain.downloads import DownloadProfile


def register(app: FastAPI, ctx: RouteContext) -> None:
    container: Container = ctx.container
    jobs = ctx.jobs
    p = ctx.p
    started = ctx.started
    svc: ForensicService = ctx.svc

    def download(*a: Any, **k: Any) -> dict[str, Any]:  # defined in a later area
        return ctx.download(*a, **k)

    def study_book(*a: Any, **k: Any) -> dict[str, Any]:  # defined in a later area
        return ctx.study_book(*a, **k)

    @app.get(f"{p}/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "backends": sorted(
                n for n, x in svc.sources.items() if n not in svc.sync_errors and getattr(x, "index_runs", True)
            ),
            "read_only": container.settings.read_only,
            "llm": container.llm.model,
            "llm_enabled": container.settings.llm.enabled,
            "llm_status": container.llm_status,
            "local_llm_url": container.settings.local_llm_url,
            # which code runs and which UI build is served: the app says when the build is stale
            "source_rev": ctx.build.source_rev,
            "web_rev": ctx.build.web_rev,
            "web_stale": ctx.build.stale,
        }

    @app.get(f"{p}/health/deep")
    def health_deep() -> dict[str, Any]:
        """Everything an operator needs at a glance: backends (with a diagnosis per failure), index, cache, disk,
        scheduler, LLM, plugin load errors. ``status``: ok | degraded (some part failing) | down (nothing usable)."""
        import shutil

        names = [b.name for b in container.settings.backends]
        bk = []
        for n in names:
            err = container.backend_errors.get(n) or svc.sync_errors.get(n)
            bk.append(
                {
                    "name": n,
                    "ok": n in svc.sources and not err,
                    "verified": n in svc.sync_ok,
                    "error": err,
                    "diagnosis": diagnosis_for(err),
                }
            )
        data_dir = container.settings.data_dir
        try:
            du = shutil.disk_usage(data_dir if data_dir.exists() else Path.cwd())
            disk = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
        except OSError:
            disk = {}
        try:
            index: dict[str, Any] = getattr(svc.index, "stats", dict)()
            index_ok = True
        except Exception as exc:  # pragma: no cover - defensive
            index, index_ok = {"error": str(exc)}, False
        sch = getattr(app.state, "scheduler", None)
        ok_b = [b for b in bk if b["ok"]]
        low_disk = bool(disk) and disk["free_gb"] < 1.0
        status = (
            "down"
            if not ok_b or not index_ok
            else ("degraded" if len(ok_b) < len(bk) or low_disk or container.settings.notices else "ok")
        )
        return {
            "status": status,
            "version": __version__,
            "uptime_s": round(time.time() - started, 1),
            "backends": bk,
            "index": index,
            "artifact_cache_bytes": svc.artifact_store.usage_bytes(),
            "disk": disk,
            "low_disk": low_disk,
            "scheduler": {"last_sync": sch.last_sync.isoformat() if sch and sch.last_sync else None},
            "llm": {"model": container.llm.model, "enabled": container.settings.llm.enabled},
            "notices": container.settings.notices,
            "jobs_running": sum(1 for j in jobs.values() if j.status == "running"),
        }

    @app.post(f"{p}/sync")
    def sync(backend: str | None = None) -> dict[str, Any]:
        job = Job(
            id=uuid.uuid4().hex[:10],
            kind="sync",
            title=f"Sync {backend or 'all backends'}",
            params={"backend": backend},
        )
        jobs[job.id] = job

        def work() -> None:
            try:
                job.result = {"indexed": svc.sync(backend)}
                job.status = "done"
            except Exception as exc:
                job.status, job.error = "failed", str(exc)

        threading.Thread(target=work, daemon=True).start()
        return {"job_id": job.id}

    @app.get(f"{p}/jobs/{{job_id}}")
    def job_status(job_id: str) -> dict[str, Any]:
        j = jobs.get(job_id)
        if not j:
            raise HTTPException(404, "job not found")
        return {
            "id": j.id,
            "kind": j.kind,
            "status": j.status,
            "progress": j.progress,
            "result": j.result,
            "error": j.error,
            "title": j.title,
            "created_at": j.created_at,
            "rerunnable": j.kind in ("download", "book", "sync") and bool(j.params or j.kind == "sync"),
        }

    @app.post(f"{p}/jobs/{{job_id}}/rerun")
    def job_rerun(job_id: str) -> dict[str, Any]:
        """Start the same job again (e.g. after it was interrupted by a restart)."""
        j = jobs.get(job_id)
        if not j:
            raise HTTPException(404, "job not found")
        pr = j.params or {}
        if j.kind == "download" and pr.get("run"):
            return download(
                str(pr["run"]),
                DownloadRequest(
                    profile=DownloadProfile(pr.get("profile", "fast_debug")),
                    paths=pr.get("paths"),
                    stages=pr.get("stages"),
                ),
            )
        if j.kind == "book" and pr.get("sid"):
            return study_book(str(pr["sid"]), bool(pr.get("ai")))
        if j.kind == "sync":
            return sync(pr.get("backend"))
        raise HTTPException(400, f"a {j.kind} job cannot be run again from here")

    @app.delete(f"{p}/jobs/{{job_id}}")
    def job_cancel(job_id: str) -> dict[str, Any]:
        j = jobs.get(job_id)
        if not j:
            raise HTTPException(404, "job not found")
        j.cancel.set()
        return {"id": j.id, "cancel_requested": True}

    @app.get(f"{p}/runs")
    def search(
        q: str = "",
        limit: int = Query(50, le=500),
        offset: int = 0,
        backend: str | None = None,
        project: str | None = None,
        top: bool = False,
    ) -> dict[str, Any]:
        """``top=true``: pipelines and plain runs; stages are listed under their pipeline (``parent:<id>``)."""
        res = svc.search(q, limit, offset, backend=backend, project=project or None, top_level=top)
        favs: set[str] = getattr(svc.index, "favorite_keys", set)()
        return {
            "total": res.total,
            "took_ms": res.took_ms,
            "notes": res.notes,
            "runs": [
                {
                    **r.model_dump(mode="json", exclude={"backend_extras", "properties", "io"}),
                    "favorite": str(r.ref) in favs,
                }
                for r in res.runs
            ],
        }

    @app.get(f"{p}/suggest")
    def suggest(prefix: str, field: str = "name") -> list[str]:
        return svc.index.suggest(prefix, field)

    from model_forensic.application.prefetch import PrefetchManager

    prefetcher = PrefetchManager(svc, container.docs)
    container.prefetcher = prefetcher
    ctx.prefetcher = prefetcher
