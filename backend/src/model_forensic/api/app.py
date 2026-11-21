"""HTTP API (FastAPI). Thin: validates input, calls ForensicService, maps errors.

Read-only by default. Long operations (sync, downloads) run as background jobs with progress and
cancellation via /jobs, so the UI never blocks on slow backends.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from model_forensic import __version__
from model_forensic.ai.investigator import Persona
from model_forensic.ai.tools import Permission, ToolBox
from model_forensic.container import Container
from model_forensic.domain.metrics import MetricKey
from model_forensic.infrastructure.artifacts.store import DownloadProfile
from model_forensic.observability.telemetry import telemetry
from model_forensic.reports.markdown import build_report, to_html
from model_forensic.shared.errors import ForensicError


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"
    progress: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


class AnalyzeRequest(BaseModel):
    run: str
    baseline: str | None = None
    analyses: list[str] | None = None
    params: dict[str, dict[str, Any]] | None = None


class InvestigateRequest(BaseModel):
    run: str
    baseline: str | None = None
    question: str | None = None
    prompt_id: str | None = None
    variables: dict[str, Any] | None = None
    persona: Persona = Persona.ANALYST
    allow_downloads: bool = False


class ReportRequest(BaseModel):
    run: str
    baseline: str | None = None
    title: str | None = None
    problem: str | None = None
    investigation_id: str | None = None
    format: str = "markdown"


class WorkspaceRequest(BaseModel):
    title: str
    runs: list[str]
    baseline: str | None = None


def create_app(container: Container) -> FastAPI:
    svc = container.service
    app = FastAPI(title="Model Forensic", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    jobs: dict[str, Job] = {}

    @app.exception_handler(ForensicError)
    async def forensic_error(_: Request, exc: ForensicError) -> JSONResponse:
        status = {
            "not_found": 404,
            "invalid_request": 400,
            "permission_denied": 403,
            "confirmation_required": 409,
            "backend_unavailable": 503,
            "insufficient_disk": 507,
        }.get(exc.code, 500)
        return JSONResponse(
            status_code=status, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        )

    @app.middleware("http")
    async def timing(request: Request, call_next: Any) -> Any:
        route = request.url.path.split("/")[3] if request.url.path.startswith("/api/v1/") else "other"
        with telemetry.timed(f"http.{route}"):
            return await call_next(request)

    p = "/api/v1"

    @app.get(f"{p}/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "backends": sorted(svc.sources),
            "read_only": container.settings.read_only,
            "llm": container.llm.model,
        }

    @app.post(f"{p}/sync")
    def sync(backend: str | None = None) -> dict[str, Any]:
        job = Job(id=uuid.uuid4().hex[:10], kind="sync")
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
        }

    @app.delete(f"{p}/jobs/{{job_id}}")
    def job_cancel(job_id: str) -> dict[str, Any]:
        j = jobs.get(job_id)
        if not j:
            raise HTTPException(404, "job not found")
        j.cancel.set()
        return {"id": j.id, "cancel_requested": True}

    @app.get(f"{p}/runs")
    def search(q: str = "", limit: int = Query(50, le=500), offset: int = 0) -> dict[str, Any]:
        res = svc.search(q, limit, offset)
        return {
            "total": res.total,
            "took_ms": res.took_ms,
            "notes": res.notes,
            "runs": [r.model_dump(mode="json", exclude={"backend_extras"}) for r in res.runs],
        }

    @app.get(f"{p}/suggest")
    def suggest(prefix: str, field: str = "name") -> list[str]:
        return svc.index.suggest(prefix, field)

    @app.get(f"{p}/runs/{{run}}")
    def overview(run: str, baseline: str | None = None) -> dict[str, Any]:
        o = svc.overview(run, baseline)
        return {
            "run": o.run.model_dump(mode="json"),
            "metric_keys": o.metric_keys,
            "summary": o.summary,
            "configs": [c.model_dump(mode="json") for c in o.configs],
            "artifacts": [a.model_dump(mode="json") for a in o.artifacts],
            "baseline": str(o.baseline) if o.baseline else None,
            "what_changed": o.what_changed.model_dump(mode="json") if o.what_changed else None,
            "findings": [f.model_dump(mode="json") for f in o.findings],
        }

    @app.put(f"{p}/runs/{{run}}/favorite")
    def favorite(run: str, value: bool = True) -> dict[str, Any]:
        svc.index.set_favorite(svc.ref(run), value)
        return {"run": run, "favorite": value}

    @app.get(f"{p}/runs/{{run}}/metrics")
    def metrics(
        run: str, keys: list[str] = Query(default=[]), max_points: int = Query(1000, le=20000)
    ) -> list[dict[str, Any]]:
        out = []
        for s in svc.series(run, [MetricKey.parse(k) for k in keys] or None):
            st, v = s.arrays()
            step = max(1, len(v) // max_points)
            out.append(
                {
                    "key": str(s.key),
                    "steps": st[::step].tolist(),
                    "values": [None if x != x else x for x in v[::step].tolist()],
                    "source": s.source,
                }
            )
        return out

    @app.get(f"{p}/runs/{{run}}/config")
    def config(run: str, flat: bool = False) -> Any:
        return svc.flat_config(run) if flat else [c.model_dump(mode="json") for c in svc.configs(run)]

    @app.get(f"{p}/runs/{{run}}/logs", response_class=PlainTextResponse)
    def logs(run: str, name: str | None = None, tail_bytes: int = Query(200_000, le=20_000_000)) -> str:
        return svc.read_log(run, name, tail_bytes)

    @app.get(f"{p}/runs/{{run}}/logs/search")
    def log_search(run: str, pattern: str) -> list[dict[str, Any]]:
        return svc.search_log(run, pattern)

    @app.post(f"{p}/runs/{{run}}/download")
    def download(run: str, profile: DownloadProfile = DownloadProfile.FAST_DEBUG) -> dict[str, Any]:
        job = Job(id=uuid.uuid4().hex[:10], kind="download")
        jobs[job.id] = job

        def work() -> None:
            try:
                files, prog = svc.download(run, profile, cancel=job.cancel)
                job.result = {
                    "files": [str(f.local_path) for f in files],
                    "cached": prog.cached_files,
                    "failed": prog.failed,
                }
                job.status = "done"
            except Exception as exc:
                job.status, job.error = "failed", str(exc)

        threading.Thread(target=work, daemon=True).start()
        container.docs.audit("api", "download", {"run": run, "profile": profile.value})
        return {"job_id": job.id}

    @app.get(f"{p}/compare")
    def compare(baseline: str, candidate: str, replicates: bool = True) -> dict[str, Any]:
        return svc.compare(baseline, candidate, use_replicates=replicates).model_dump(mode="json")

    @app.get(f"{p}/analyses")
    def analyses() -> list[dict[str, Any]]:
        return [
            {
                "name": a.name,
                "version": a.version,
                "title": a.title,
                "description": a.description,
                "category": a.category,
                "requires": sorted(r.value for r in a.requires),
                "limitations": list(a.limitations),
                "params": a.Params.model_json_schema(),
            }
            for a in svc.analyses.all()
        ]

    @app.post(f"{p}/analyze")
    def analyze(req: AnalyzeRequest) -> list[dict[str, Any]]:
        res = svc.analyze(req.run, req.baseline, req.analyses, req.params)
        return [
            {
                "analysis": r.analysis,
                "skipped_reason": r.skipped_reason,
                "notes": r.notes,
                "findings": [f.model_dump(mode="json") for f in r.findings],
                "plots": [pl.model_dump() for pl in r.plots],
            }
            for r in res
        ]

    @app.get(f"{p}/findings/{{finding_id}}")
    def finding(finding_id: str) -> dict[str, Any]:
        return svc.findings.get(finding_id).model_dump(mode="json")

    @app.get(f"{p}/prompts")
    def prompts(tag: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "id": t.id,
                "version": t.version,
                "title": t.title,
                "tags": list(t.tags),
                "requires": list(t.requires),
                "template": t.template,
            }
            for t in container.prompts.all(tag)
        ]

    @app.post(f"{p}/investigate")
    def investigate(req: InvestigateRequest) -> dict[str, Any]:
        allow = {Permission.READ} | ({Permission.EXPENSIVE} if req.allow_downloads else set())
        tb = ToolBox(svc, allow=frozenset(allow), audit=lambda a, d: container.docs.audit("ai", a, d))
        inv = container.investigator().investigate(
            req.run,
            req.question,
            baseline=req.baseline,
            prompt_id=req.prompt_id,
            variables=req.variables,
            persona=req.persona,
            toolbox=tb,
        )
        d = inv.to_dict()
        container.docs.put(
            "investigation", inv.id, d, [svc.ref(req.run), *([svc.ref(req.baseline)] if req.baseline else [])]
        )
        return d

    @app.post(f"{p}/reports")
    def report(req: ReportRequest) -> Any:
        results = svc.analyze(req.run, req.baseline)
        runs = [("candidate" if req.baseline else "target", svc.get_run(req.run))]
        if req.baseline:
            runs.insert(0, ("baseline", svc.get_run(req.baseline)))
        cmp = svc.compare(req.baseline, req.run) if req.baseline else None
        inv = container.docs.get_raw("investigation", req.investigation_id) if req.investigation_id else None
        title = req.title or f"Forensic report: {req.run}" + (f" vs {req.baseline}" if req.baseline else "")
        md = build_report(title, runs, results, cmp, inv, req.problem)
        rid = uuid.uuid4().hex[:10]
        container.docs.put("report", rid, {"id": rid, "title": title, "markdown": md}, [svc.ref(req.run)])
        if req.format == "html":
            return HTMLResponse(to_html(md, title))
        return PlainTextResponse(md, media_type="text/markdown")

    @app.post(f"{p}/workspaces")
    def create_ws(req: WorkspaceRequest) -> dict[str, Any]:
        ws = svc.create_workspace(f"ws-{uuid.uuid4().hex[:8]}", req.title, req.runs, req.baseline)
        return ws.model_dump(mode="json")

    @app.get(f"{p}/workspaces")
    def list_ws() -> list[dict[str, Any]]:
        return [w.model_dump(mode="json") for w in svc.workspaces.list()]

    @app.get(f"{p}/workspaces/{{ws_id}}")
    def get_ws(ws_id: str) -> dict[str, Any]:
        return svc.workspaces.get(ws_id).model_dump(mode="json")

    @app.get(f"{p}/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return {
            "telemetry": telemetry.snapshot(),
            "index": getattr(svc.index, "stats", dict)(),
            "artifact_cache_bytes": svc.artifact_store.usage_bytes(),
            "audit": container.docs.audit_log(50),
        }

    return app
