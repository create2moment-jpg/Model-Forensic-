"""HTTP API (FastAPI). Thin: validates input, calls ForensicService, maps errors.

Read-only by default. Long operations (sync, downloads) run as background jobs with progress and
cancellation via /jobs, so the UI never blocks on slow backends.
"""

from __future__ import annotations

import contextlib
import json
import math
import mimetypes
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from model_forensic import __version__
from model_forensic.ai.investigator import Persona
from model_forensic.ai.tools import Permission, ToolBox
from model_forensic.api.auth import OPEN_PATHS, Authenticator, AuthError
from model_forensic.application.ports import ListRunsQuery
from model_forensic.container import Container
from model_forensic.domain.metrics import MetricKey
from model_forensic.domain.study import EvidenceLink, HypothesisStatus
from model_forensic.infrastructure.artifacts.store import DownloadProfile
from model_forensic.observability.telemetry import telemetry
from model_forensic.reports.markdown import build_report, to_html
from model_forensic.settings import LLMConfig
from model_forensic.shared import identity
from model_forensic.shared.downsample import m4_indices
from model_forensic.shared.errors import ForensicError
from model_forensic.shared.logging import get_logger


@dataclass
class Job:
    id: str
    kind: str
    title: str = ""
    created_at: float = field(default_factory=time.time)
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
    others: list[str] | None = None  # extra runs to compare in one question (max 8)


class ReportRequest(BaseModel):
    run: str
    baseline: str | None = None
    title: str | None = None
    problem: str | None = None
    investigation_id: str | None = None
    format: str = "markdown"


class DownloadRequest(BaseModel):
    profile: DownloadProfile = DownloadProfile.FAST_DEBUG
    paths: list[str] | None = None  # "path" or "<stage run>::<path>"
    stages: list[str] | None = None  # pipeline stages to include (default: all)


class LLMSettingsRequest(BaseModel):
    enabled: bool
    base_url: str
    model: str
    api_key: str | None = None  # write-only; None = keep current, "" = clear
    api_key_env: str = "MF_LLM_API_KEY"
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout_s: float = 120.0
    context_limit_tokens: int = 64_000
    reasoning_effort: str | None = None
    azure_api_version: str | None = None
    max_tool_steps: int = 4


class StudyCreate(BaseModel):
    title: str
    goal: str = ""
    runs: list[str] = []
    baseline: str | None = None


class StudyPatch(BaseModel):
    title: str | None = None
    goal: str | None = None
    baseline: str | None = None  # "" clears
    status: str | None = None  # open | closed


class RunBody(BaseModel):
    run: str


class TextBody(BaseModel):
    text: str


class AttachInvestigation(BaseModel):
    investigation_id: str


class HypothesisCreate(BaseModel):
    statement: str
    rationale: str = ""
    proposed_change: str = ""
    evidence: list[EvidenceLink] = []
    source: str = "manual"
    reference_run: str | None = None


class HypothesisPatch(BaseModel):
    statement: str | None = None
    status: HypothesisStatus | None = None
    rationale: str | None = None
    proposed_change: str | None = None
    verdict: str | None = None
    test_run: str | None = None  # "" unlinks
    reference_run: str | None = None


log = get_logger(__name__)


def create_app(container: Container) -> FastAPI:
    svc = container.service
    app = FastAPI(title="Model Forensic", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
        details = dict(exc.details or {})
        if exc.code == "backend_unavailable" and "diagnosis" not in details:
            details["diagnosis"] = _diagnosis(f"{exc.message} {exc.__cause__ or ''}")
        return JSONResponse(
            status_code=status, content={"error": {"code": exc.code, "message": exc.message, "details": details}}
        )

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"error": {"code": "invalid_request", "message": str(exc)[:500], "details": {}}}
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Never a stack trace to the client: a short message, an id to find the trace in the server log."""
        rid = uuid.uuid4().hex[:12]
        log.error(
            "unhandled error", request_id=rid, path=request.url.path, error=f"{type(exc).__name__}: {exc}", exc_info=exc
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal",
                    "message": f"Unexpected error ({type(exc).__name__}). "
                    f"Reference {rid} — see the server log; Diagnostics shows recent errors.",
                    "details": {"request_id": rid},
                }
            },
        )

    server = container.settings.server
    authn = Authenticator(server) if server.mode == "shared" else None
    from model_forensic.application.scheduler import Scheduler

    app.state.scheduler = Scheduler(container)
    admin_paths = ("/api/v1/settings/azure", "/api/v1/settings/llm", "/api/v1/analyses/reload")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        """Shared mode: every API call carries a user (proxy header or OIDC token); settings need an admin."""
        if authn is None or not request.url.path.startswith("/api/") or request.url.path in OPEN_PATHS:
            return await call_next(request)
        try:
            user = authn.user(request.headers)
        except AuthError as exc:
            return JSONResponse(
                status_code=exc.status, content={"error": {"code": "unauthenticated", "message": str(exc)}}
            )
        if (
            request.method in {"PUT", "POST", "DELETE", "PATCH"}
            and request.url.path.startswith(admin_paths)
            and not user.admin
        ):
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "permission_denied", "message": "only admins can change settings"}},
            )
        token = identity.set_current(user)
        try:
            return await call_next(request)
        finally:
            identity.reset(token)

    @app.middleware("http")
    async def timing(request: Request, call_next: Any) -> Any:
        route = request.url.path.split("/")[3] if request.url.path.startswith("/api/v1/") else "other"
        with telemetry.timed(f"http.{route}"):
            return await call_next(request)

    p = "/api/v1"
    started = time.time()

    @app.get(f"{p}/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "backends": sorted(n for n in svc.sources if n not in svc.sync_errors),
            "read_only": container.settings.read_only,
            "llm": container.llm.model,
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
                    "diagnosis": _diagnosis(err),
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
        job = Job(id=uuid.uuid4().hex[:10], kind="sync", title=f"Sync {backend or 'all backends'}")
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
    def search(
        q: str = "", limit: int = Query(50, le=500), offset: int = 0, backend: str | None = None
    ) -> dict[str, Any]:
        res = svc.search(q, limit, offset, backend=backend)
        favs: set[str] = getattr(svc.index, "favorite_keys", set)()
        return {
            "total": res.total,
            "took_ms": res.took_ms,
            "notes": res.notes,
            "runs": [
                {**r.model_dump(mode="json", exclude={"backend_extras"}), "favorite": str(r.ref) in favs}
                for r in res.runs
            ],
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
            idx = m4_indices(v, max_points)
            out.append(
                {
                    "key": str(s.key),
                    "steps": st[idx].tolist(),
                    "values": [x if math.isfinite(x) else None for x in v[idx].tolist()],
                    "timestamps": [s.timestamps[i] for i in idx.tolist()] if s.timestamps else None,
                    "source": s.source,
                    "points_total": int(v.size),
                    "non_finite": int((~np.isfinite(v)).sum()),
                }
            )
        return out

    @app.get(f"{p}/run-info")
    def run_info(runs: list[str] = Query(default=[])) -> list[dict[str, Any]]:
        """Lightweight metadata for several runs (no comparison/analysis work)."""
        if len(runs) > 100:
            raise HTTPException(400, "at most 100 runs")
        return [svc.get_run(r).model_dump(mode="json") for r in runs]

    @app.get(f"{p}/metric-keys")
    def metric_keys(runs: list[str] = Query(default=[])) -> dict[str, int]:
        if len(runs) > 100:
            raise HTTPException(400, "at most 100 runs")
        return svc.metric_keys(runs)

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
                    "diagnosis": _diagnosis(container.backend_errors.get(b.name) or svc.sync_errors.get(b.name)),
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
            return check_run(svc, run)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get(f"{p}/glossary")
    def glossary() -> dict[str, str]:
        from model_forensic.application.check_run import GLOSSARY

        return GLOSSARY

    @app.get(f"{p}/settings/team")
    def team_settings() -> dict[str, Any]:
        """Team policy (config/team.local.yaml): de-prioritised classes, expected metrics, code refs, defaults."""
        from model_forensic.interface import team

        return {**svc.team.model_dump(), "path": str(team.path())}

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

    @app.get(f"{p}/runs/{{run}}/dataset")
    def run_dataset_api(run: str, refresh: bool = False) -> dict[str, Any]:
        """Requested (config) vs loaded (driver log) dataset per stage."""
        from model_forensic.application.run_info import run_dataset

        return run_dataset(svc, run, refresh)

    @app.get(f"{p}/runs/{{run}}/compute")
    def run_compute_api(run: str, refresh: bool = False) -> dict[str, Any]:
        """GPUs actually used (driver log), requested parallelism (config), compute target — per stage."""
        from model_forensic.application.run_info import run_compute

        return run_compute(svc, run, refresh)

    @app.get(f"{p}/runs/{{run}}/stages")
    def stages(run: str) -> list[dict[str, Any]]:
        """Pipeline stages (child runs) in order; empty for non-pipeline runs."""
        out = []
        for name, ref in svc.stages(run):
            try:
                child = svc.get_run(ref)
                out.append(
                    {
                        "name": name,
                        "ref": str(ref),
                        "status": child.status.value,
                        "kind": child.kind.value,
                        "duration_s": child.duration_s,
                        "failure": child.failure.message if child.failure else None,
                    }
                )
            except Exception as exc:
                out.append(
                    {
                        "name": name,
                        "ref": str(ref),
                        "status": "unknown",
                        "kind": "other",
                        "duration_s": None,
                        "failure": f"{type(exc).__name__}",
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
    def download(run: str, req: DownloadRequest | None = None) -> dict[str, Any]:
        req = req or DownloadRequest()
        job = Job(
            id=uuid.uuid4().hex[:10],
            kind="download",
            title=f"{req.profile.value if not req.paths else f'{len(req.paths)} file(s)'} · {run}",
        )
        jobs[job.id] = job

        def progress(pr: Any) -> None:
            job.progress = {
                "done_files": pr.done_files,
                "total_files": pr.total_files,
                "done_bytes": pr.done_bytes,
                "total_bytes": pr.total_bytes,
                "cached_files": pr.cached_files,
                "throughput_bps": round(pr.throughput_bps),
            }

        def work() -> None:
            try:
                files, prog = svc.download(
                    run, req.profile, cancel=job.cancel, on_progress=progress, paths=req.paths, stages=req.stages
                )
                progress(prog)
                job.result = {
                    "files": [
                        {
                            "path": f.artifact.path,
                            "run": str(f.artifact.run),
                            "stage": f.artifact.stage,
                            "local_path": str(f.local_path),
                            "sha256": f.sha256,
                            "from_cache": f.from_cache,
                        }
                        for f in files
                    ],
                    "cached": prog.cached_files,
                    "failed": prog.failed,
                }
                job.status = "done"
            except Exception as exc:
                job.status, job.error = ("cancelled" if job.cancel.is_set() else "failed"), str(exc)

        threading.Thread(target=work, daemon=True).start()
        container.docs.audit("api", "download", {"run": run, "profile": req.profile.value, "paths": req.paths})
        return {"job_id": job.id}

    @app.get(f"{p}/runs/{{run}}/artifacts/file")
    def artifact_file(run: str, path: str) -> Any:
        """Stream one artifact to the browser (cached copy if present, otherwise from the backend)."""
        ref = svc.ref(run)
        art = svc.find_artifact(ref, path)  # "path" or "<stage run>::<path>" for a pipeline stage's file
        if art is None:
            raise HTTPException(404, "artifact not found")
        name = PurePosixPath(art.path).name.replace('"', "") or "artifact"
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        headers = {"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"}
        hit = svc.artifact_store.cached(art)
        if hit is not None:
            return FileResponse(hit.local_path, media_type=media, headers=headers)
        container.docs.audit("api", "artifact_stream", {"run": run, "path": path})
        return StreamingResponse(
            svc.source(art.run).open_artifact(art.run.run_id, art.path), media_type=media, headers=headers
        )

    @app.get(f"{p}/runs/{{run}}/download/plans")
    def download_plans(run: str, stages: list[str] = Query(default=[])) -> dict[str, Any]:
        """What each download profile would fetch (files, bytes, already cached) — shown before downloading."""
        return {
            "profiles": svc.plans(run, stages or None),
            "stages": [n for n, _ in svc.stages(run)],
            "local_root": str(svc.artifact_store.run_root(svc.ref(run))),
        }

    @app.get(f"{p}/runs/{{run}}/sequences")
    def sequences(
        run: str,
        table: int | None = None,
        metric: str | None = None,
        cls: str | None = None,
        limit: int = Query(25, le=500),
    ) -> dict[str, Any]:
        """Per-sequence evaluation tables (parquets, downloaded once): per-class summary and worst sequences."""
        from model_forensic.analysis import sequences as sq

        tables = svc.eval_tables(run)
        out: dict[str, Any] = {
            "tables": [sq.summary(t, worst_n=0) for t in tables],
            "deprioritized": svc.team.deprioritized_classes,
        }
        rec = [i for i, t in enumerate(tables) if t.recognized]
        det = [i for i in rec if tables[i].kind == "detection"]
        if rec:
            # Default: the last stage's detection table, else the last recognised table.
            default = det[-1] if det else rec[-1]
            i = table if table is not None and 0 <= table < len(tables) and tables[table].recognized else default
            out["selected"] = i
            out["worst"] = sq.worst(tables[i], metric, limit, cls)
        return out

    @app.post(f"{p}/runs/{{run}}/metrics/export")
    def export_metrics(run: str) -> dict[str, str]:
        """Write all metric histories (stages merged, raw names kept) as JSON and CSV next to the run's files."""
        j, c = svc.export_metrics(run)
        container.docs.audit("api", "export_metrics", {"run": run})
        return {"json": str(j), "csv": str(c)}

    @app.get(f"{p}/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return [
            {
                "id": j.id,
                "kind": j.kind,
                "title": j.title,
                "status": j.status,
                "progress": j.progress,
                "error": j.error,
                "created_at": j.created_at,
            }
            for j in sorted(jobs.values(), key=lambda j: -j.created_at)[:50]
        ]

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
        data = req.model_dump(exclude={"api_key"})
        container.configure_llm(LLMConfig(**data), req.api_key)
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
        docs = container.docs.list_raw("investigation")
        if run:
            ids = set(container.docs.ids_for_runs("investigation", [svc.ref(run)]))
            docs = [d for d in docs if d.get("id") in ids]
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

    @app.get(f"{p}/compare")
    def compare(baseline: str, candidate: str, replicates: bool = True) -> dict[str, Any]:
        return svc.compare(baseline, candidate, use_replicates=replicates).model_dump(mode="json")

    def _curves(runs: list[str], keys: list[str] | None, max_points: int = 600) -> dict[str, Any]:
        from model_forensic.analysis.divergence import compare_curves

        if not 2 <= len(runs) <= 10:
            raise HTTPException(400, "compare 2 to 10 runs")
        info: list[dict[str, Any]] = []
        labels: dict[str, str] = {}
        for r in runs:
            run = svc.get_run(r)
            lab = run.display_name or run.name
            lab = lab if lab not in labels.values() else f"{lab} ({r})"
            labels[r] = lab
            info.append({"ref": str(run.ref), "label": lab, "status": run.status.value})
        if not keys:
            counts = svc.metric_keys(runs)
            common = [k for k, n in counts.items() if n == len(runs) and "[" not in k]
            pick = [k for k in ("train/loss",) if k in common]
            pick += [
                k
                for k in common
                if k.startswith(("val/", "test/"))
                and svc.metrics.lookup(MetricKey.parse(k).name).higher_is_better is not None
            ][: 4 - len(pick)]
            keys = pick or common[:4]
        ref_run = runs[0]
        lrs = {}
        for r in runs:
            try:
                lrs[labels[r]] = svc.lr_series(r)
            except Exception:  # LR is optional context
                lrs[labels[r]] = None
        changes = {}
        for r in runs[1:]:
            try:
                changes[labels[r]] = svc.diff_configs(ref_run, r).changes
            except Exception:
                changes[labels[r]] = ()
        out_keys = []
        for key in keys[:6]:
            mk = MetricKey.parse(key)
            series, charts = {}, []
            for r in runs:
                got = svc.series(r, [mk])
                if not got:
                    continue
                series[labels[r]] = got[0]
                st, v = got[0].arrays()
                idx = m4_indices(v, max_points)
                charts.append(
                    {
                        "run": labels[r],
                        "ref": r,
                        "steps": st[idx].tolist(),
                        "values": [x if math.isfinite(x) else None for x in v[idx].tolist()],
                    }
                )
            cmp = compare_curves(key, labels[ref_run], series, registry=svc.metrics, lrs=lrs, changes=changes)
            out_keys.append({**cmp.to_dict(), "series": charts})
        return {"reference": labels[ref_run], "runs": info, "keys": out_keys}

    @app.post(f"{p}/compare/curves")
    def compare_curves_api(body: dict[str, Any]) -> dict[str, Any]:
        """Where and why 2–10 runs diverge from the first (reference) run, per metric."""
        keys = [str(k) for k in body.get("keys") or []] or None
        return _curves([str(r) for r in body.get("runs", [])], keys)

    @app.post(f"{p}/compare/ask")
    def compare_ask(body: dict[str, Any]) -> dict[str, Any]:
        """A grounded answer about a curves comparison; the evidence is its deterministic reading."""
        from dataclasses import asdict

        from model_forensic.ai.copilot import ask_chart as ask
        from model_forensic.ai.fake_llm import OfflineHeuristicLLM

        keys = [str(k) for k in body.get("keys") or []] or None
        res = _curves([str(r) for r in body.get("runs", [])], keys)
        lines: list[str] = []
        for k in res["keys"]:
            lines.append(k["summary"])
            for r in k["rows"]:
                lines.append(f"{k['key']}: {r['sentence']}" + (f" ({r['lr_note']})" if r["lr_note"] else ""))
                if r["fits"] and r["outcome"] not in ("same", "insufficient"):
                    lines.append(
                        f"{k['key']}: config changes of {r['run']} that fit the timing: " + "; ".join(r["fits"])
                    )
        evidence = [{"id": f"E{i + 1}", "text": t} for i, t in enumerate(lines)]
        if isinstance(container.llm, OfflineHeuristicLLM) or not lines:
            recs = []
            for k in res["keys"]:
                for r in k["rows"]:
                    if r["outcome"] in ("ahead", "behind", "crossed") and r["fits"]:
                        recs.append(
                            f"To test why {r['run']} is {r['outcome']} on {k['key']}: re-run the reference with "
                            f"only '{r['fits'][0]}' changed."
                        )
            return {
                "summary": "No LLM is configured, so here is the rule-based reading: "
                + (lines[0] if lines else "nothing to compare."),
                "claims": [
                    {
                        "type": "RECOMMENDATION",
                        "text": t,
                        "evidence": ["E1"],
                        "confidence": None,
                        "verified": True,
                        "problems": [],
                    }
                    for t in dict.fromkeys(recs)
                ][:5],
                "evidence": evidence,
                "model": container.llm.model,
            }
        ans = ask(container.llm, "comparison", str(body.get("question", "")), lines)
        container.docs.audit(identity.current().id, "compare_ask", {"runs": body.get("runs", [])})
        assert ans is not None
        return {
            "summary": ans.summary,
            "claims": [asdict(c) for c in ans.claims],
            "verified_ratio": ans.verified_ratio,
            "evidence": evidence,
            "model": container.llm.model,
        }

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
                "variables": sorted(t.variables - {"run_id", "baseline_run_id", "run_ids"}),
                "category": t.category,
                "description": t.description,
                "pack": t.pack,
                "needs_baseline": t.needs_baseline,
                "include": {
                    "logs": t.include.logs,
                    "series": list(t.include.series),
                    "config_keys": list(t.include.config_keys),
                    "slices": t.include.slices,
                },
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
            others=req.others,
        )
        d = inv.to_dict()
        container.docs.put(
            "investigation",
            inv.id,
            d,
            [
                svc.ref(req.run),
                *([svc.ref(req.baseline)] if req.baseline else []),
                *(svc.ref(o) for o in req.others or []),
            ],
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

    # ------------------------------------------------------------------ studies
    studies = container.studies

    def sdump(st: Any) -> dict[str, Any]:
        return st.model_dump(mode="json")  # type: ignore[no-any-return]

    @app.get(f"{p}/studies")
    def list_studies() -> list[dict[str, Any]]:
        return [
            {
                "id": st.id,
                "title": st.title,
                "goal": st.goal,
                "updated_at": st.updated_at.isoformat(),
                "runs": len(st.runs),
                "hypotheses": {k.value: sum(1 for h in st.hypotheses if h.status == k) for k in HypothesisStatus},
                "status": st.status,
            }
            for st in sorted(studies.all(), key=lambda x: (x.status == "closed", -x.updated_at.timestamp()))
        ]

    @app.post(f"{p}/studies")
    def create_study(req: StudyCreate) -> dict[str, Any]:
        return sdump(studies.create(req.title, req.goal, req.runs, req.baseline))

    def _finding_run(f: Any) -> str:
        """Provenance lists [baseline, candidate, replicates…] for comparisons, [run] otherwise."""
        rs = f.provenance.runs
        return str(rs[1] if len(rs) > 1 else rs[0]) if rs else ""

    @app.post(f"{p}/findings/{{fid}}/ask")
    def ask_finding(fid: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """A grounded investigation focused on one finding."""
        from model_forensic.ai.copilot import focus_question

        body = body or {}
        f = svc.findings.get(fid)
        run = str(body.get("run") or _finding_run(f))
        if not run:
            raise HTTPException(400, "which run?")
        inv = container.investigator().investigate(
            run, focus_question(str(body.get("question", "")), f), baseline=body.get("baseline")
        )
        container.docs.audit("api", "ask_finding", {"finding": fid, "run": run})
        return inv.to_dict()

    def _chart_reading(run: str, key: str, baseline: str | None) -> Any:
        from model_forensic.analysis.chart_insights import explain

        mk = MetricKey.parse(key)
        got = svc.series(run, [mk])
        if not got:
            raise HTTPException(404, f"{key} is not logged by {run}")
        base = svc.series(baseline, [mk]) if baseline else []
        lr = None
        if mk.split.value == "train":
            try:
                lr = svc.lr_series(run)
            except Exception:  # the LR is optional context
                lr = None
        return explain(got[0], registry=svc.metrics, lr=lr, baseline=base[0] if base else None)

    @app.get(f"{p}/runs/{{run}}/metrics/explain")
    def explain_chart(run: str, key: str, baseline: str | None = None) -> dict[str, Any]:
        """'Explain this chart': deterministic facts, warnings and markers for one metric of one run."""
        return dict(_chart_reading(run, key, baseline).to_dict())

    @app.post(f"{p}/runs/{{run}}/metrics/ask")
    def ask_chart(run: str, body: dict[str, Any]) -> dict[str, Any]:
        """A grounded answer about one chart: the evidence is the chart's own deterministic reading."""
        from dataclasses import asdict

        from model_forensic.ai.copilot import ask_chart as ask

        key = str(body.get("key", ""))
        reading = _chart_reading(run, key, body.get("baseline"))
        lines = [f"{key}: {x}" for x in reading.facts + reading.warnings]
        from model_forensic.ai.fake_llm import OfflineHeuristicLLM

        if isinstance(container.llm, OfflineHeuristicLLM):  # no LLM: the rule-based checks, citing the reading
            n_f = len(reading.facts)
            cite = [f"E{n_f + i + 1}" for i in range(len(reading.warnings))] or ["E1"]
            return {
                "summary": (
                    "No LLM is configured, so here is the rule-based reading: "
                    + (reading.warnings[0] if reading.warnings else "nothing unusual on this chart.")
                ),
                "claims": [
                    {
                        "type": "RECOMMENDATION",
                        "text": t,
                        "evidence": cite[:2],
                        "confidence": None,
                        "verified": True,
                        "problems": [],
                    }
                    for t in reading.checks
                ],
                "verified_ratio": 1.0,
                "insufficient_evidence": not lines,
                "evidence": [{"id": f"E{i + 1}", "text": t} for i, t in enumerate(lines)],
                "model": container.llm.model,
            }
        ans = ask(container.llm, key, str(body.get("question", "")), lines)
        container.docs.audit(identity.current().id, "ask_chart", {"run": run, "key": key})
        if ans is None:
            return {"summary": "Nothing to explain: the chart has no values.", "claims": [], "evidence": []}
        return {
            "summary": ans.summary,
            "claims": [asdict(c) for c in ans.claims],
            "verified_ratio": ans.verified_ratio,
            "insufficient_evidence": ans.insufficient_evidence,
            "evidence": [{"id": f"E{i + 1}", "text": t} for i, t in enumerate(lines)],
            "model": container.llm.model,
        }

    def _runs_reading(key: str, runs: list[str]) -> Any:
        from model_forensic.analysis.chart_insights import explain_runs

        if not 2 <= len(runs) <= 50:
            raise HTTPException(400, "2 to 50 runs")
        mk = MetricKey.parse(key)
        series: dict[str, Any] = {}
        running: set[str] = set()
        for r in runs:
            got = svc.series(r, [mk])
            if got:
                run = svc.get_run(r)
                label = run.display_name or run.name
                label = label if label not in series else f"{label} ({r})"
                series[label] = got[0]
                if not run.status.terminal:
                    running.add(label)
        return explain_runs(key, series, registry=svc.metrics, running=running)

    @app.post(f"{p}/metrics/explain-runs")
    def explain_runs_chart(body: dict[str, Any]) -> dict[str, Any]:
        """'Explain this chart' for a multi-run chart (Progression): ranking, spread vs noise, NaN/diverged runs."""
        return dict(_runs_reading(str(body.get("key", "")), [str(x) for x in body.get("runs", [])]).to_dict())

    @app.post(f"{p}/metrics/ask-runs")
    def ask_runs_chart(body: dict[str, Any]) -> dict[str, Any]:
        """A grounded answer about a multi-run chart; the evidence is its cross-run reading."""
        from dataclasses import asdict

        from model_forensic.ai.copilot import ask_chart as ask
        from model_forensic.ai.fake_llm import OfflineHeuristicLLM

        key = str(body.get("key", ""))
        reading = _runs_reading(key, [str(x) for x in body.get("runs", [])])
        lines = [f"{key}: {x}" for x in reading.facts + reading.warnings]
        evidence = [{"id": f"E{i + 1}", "text": t} for i, t in enumerate(lines)]
        if isinstance(container.llm, OfflineHeuristicLLM) or not lines:
            return {
                "summary": "No LLM is configured, so here is the rule-based reading: "
                + (reading.warnings[0] if reading.warnings else (reading.facts[0] if reading.facts else "no data.")),
                "claims": [
                    {
                        "type": "RECOMMENDATION",
                        "text": t,
                        "evidence": ["E1"] if lines else [],
                        "confidence": None,
                        "verified": bool(lines),
                        "problems": [],
                    }
                    for t in reading.checks
                ],
                "evidence": evidence,
                "model": container.llm.model,
            }
        ans = ask(container.llm, key, str(body.get("question", "")), lines)
        container.docs.audit(identity.current().id, "ask_runs_chart", {"key": key})
        assert ans is not None
        return {
            "summary": ans.summary,
            "claims": [asdict(c) for c in ans.claims],
            "verified_ratio": ans.verified_ratio,
            "evidence": evidence,
            "model": container.llm.model,
        }

    @app.get(f"{p}/findings/{{fid}}/explain")
    def explain_finding(fid: str) -> dict[str, Any]:
        """Why this confidence, and what would change it."""
        from model_forensic.application.feedback import explain_confidence

        return explain_confidence(svc.findings.get(fid))

    @app.put(f"{p}/findings/{{fid}}/feedback")
    def finding_feedback(fid: str, body: dict[str, Any]) -> dict[str, Any]:
        """ "This finding is wrong / correct" — stored locally, summarised in GET /feedback/report."""
        from model_forensic.application.feedback import record

        return record(container.docs, svc.findings.get(fid), str(body.get("verdict", "")), str(body.get("note", "")))

    @app.get(f"{p}/feedback/report")
    def feedback_report() -> dict[str, Any]:
        from model_forensic.application.feedback import report

        return report(container.docs)

    @app.post(f"{p}/findings/{{fid}}/next-experiment")
    def next_experiment(fid: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """The next run as a config diff (rule-based or AI-proposed and validated). Shown, never submitted."""
        from dataclasses import asdict

        from model_forensic.ai.copilot import next_experiment as propose
        from model_forensic.ai.fake_llm import OfflineHeuristicLLM

        body = body or {}
        f = svc.findings.get(fid)
        run = str(body.get("run") or _finding_run(f))
        llm = None if isinstance(container.llm, OfflineHeuristicLLM) else container.llm
        ne = propose(
            svc,
            llm,
            run,
            f,
            body.get("baseline") or (str(f.provenance.runs[0]) if len(f.provenance.runs) > 1 else None),
        )
        return {**asdict(ne), "yaml": ne.yaml_patch()}

    @app.post(f"{p}/analyses/reload")
    def reload_analyses() -> dict[str, Any]:
        """Re-read your own analyses from the plugin folders (config/plugins/*.py)."""
        from model_forensic.analysis.local_plugins import install

        svc.local_plugins, errors = install(svc.analyses, list(container.settings.plugin_dirs), svc.local_plugins)
        return {"local": sorted(svc.local_plugins), "errors": errors}

    @app.get(f"{p}/noise")
    def noise(runs: list[str] = Query(default=[]), candidate: str | None = None) -> list[dict[str, Any]]:
        """Noise floor across replicate runs; with ``candidate`` the effect sizes against it."""
        from dataclasses import asdict

        from model_forensic.application.noise import effects, noise_floor

        if len(runs) < 2:
            raise HTTPException(400, "need at least two replicate runs")
        rows = effects(svc, runs, candidate) if candidate else noise_floor(svc, runs)
        return [asdict(r) for r in rows]

    @app.post(f"{p}/gate")
    def gate_config(body: dict[str, Any]) -> list[dict[str, Any]]:
        """Launch-time checks on a config (YAML text), optionally against a reference run."""
        from model_forensic.application.gate import gate
        from model_forensic.shared import yamlsafe

        text = str(body.get("config", ""))
        if len(text) > 2_000_000:
            raise HTTPException(413, "config too large")
        cfg = yamlsafe.load(text)
        if not isinstance(cfg, dict):
            raise HTTPException(400, "config is not a YAML mapping")
        ref = body.get("against")
        logged = [s.provenance.get("raw_name") or str(s.key) for s in svc.series(ref)] if ref else None
        res = gate(cfg, logged, {"ref": svc.flat_config(ref)} if ref else None)
        return [r.__dict__ for r in res]

    @app.get(f"{p}/study-templates")
    def study_templates() -> list[dict[str, Any]]:
        from model_forensic.application.study_templates import TEMPLATES

        return [t.as_dict() for t in TEMPLATES]

    @app.post(f"{p}/studies/wizard")
    def study_wizard(body: dict[str, Any]) -> Any:
        """Create a study from a question template with a pre-registered claim, metric, falsifier, confounders."""
        st = container.studies.create_from_template(
            str(body.get("template", "")),
            str(body.get("title", "")),
            [str(r) for r in body.get("runs") or []],
            body.get("baseline"),
            str(body.get("claim", "")),
            str(body.get("deciding_metric", "")),
            str(body.get("falsified_if", "")),
            body.get("confounders"),
            str(body.get("goal", "")),
        )
        return st.model_dump(mode="json")

    @app.post(f"{p}/studies/{{sid}}/gate")
    def study_gate(sid: str) -> Any:
        """Run the pitfall checks P1–P8 over every study run; failing measurement checks block a verdict."""
        return container.studies.run_gate(sid).model_dump(mode="json")

    @app.put(f"{p}/studies/{{sid}}/confounders")
    def study_confounder(sid: str, body: dict[str, Any]) -> Any:
        return container.studies.check_confounder(
            sid, str(body.get("confounder", "")), bool(body.get("checked", True))
        ).model_dump(mode="json")

    @app.put(f"{p}/studies/{{sid}}/decision")
    def study_decision(sid: str, body: dict[str, Any]) -> Any:
        return container.studies.decide(
            sid,
            str(body.get("outcome", "")),
            str(body.get("decision", "")),
            str(body.get("rationale", "")),
            [str(x) for x in body.get("based_on") or []],
            str(body.get("gate_override", "")),
        ).model_dump(mode="json")

    @app.post(f"{p}/studies/{{sid}}/book")
    def study_book(sid: str, ai: bool = False) -> dict[str, Any]:
        """Generate the study book in the background (evaluation files are downloaded once, then cached)."""
        container.studies.get(sid)
        job = Job(id=uuid.uuid4().hex[:10], kind="book", title=f"Study book · {sid}")
        jobs[job.id] = job

        def work() -> None:
            try:
                html_path, summary_path = container.study_book(
                    sid, ai=ai, progress=lambda m: job.progress.update({"step": m})
                )
                job.result = {"html": str(html_path), "summary": json.loads(summary_path.read_text())}
                job.status = "done"
            except Exception as exc:
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"

        threading.Thread(target=work, daemon=True).start()
        container.docs.audit("api", "study_book", {"study": sid, "ai": ai})
        return {"job_id": job.id}

    @app.get(f"{p}/studies/{{sid}}/book", response_class=HTMLResponse)
    def study_book_html(sid: str) -> Any:
        path = container.settings.data_dir / "books" / sid / "index.html"
        if not path.is_file():
            raise HTTPException(404, "no book generated yet")
        return HTMLResponse(
            path.read_text(encoding="utf-8"),
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(f"{p}/studies/{{sid}}")
    def get_study(sid: str) -> dict[str, Any]:
        return sdump(studies.get(sid))

    @app.patch(f"{p}/studies/{{sid}}")
    def patch_study(sid: str, req: StudyPatch) -> dict[str, Any]:
        st = studies.update(sid, req.title, req.goal)
        if req.baseline is not None:
            st = studies.set_baseline(sid, req.baseline or None)
        if req.status is not None:
            st = studies.set_status(sid, req.status)
        return sdump(st)

    @app.delete(f"{p}/studies/{{sid}}")
    def delete_study(sid: str) -> dict[str, Any]:
        studies.delete(sid)
        return {"deleted": sid}

    @app.post(f"{p}/studies/{{sid}}/runs")
    def study_add_run(sid: str, req: RunBody) -> dict[str, Any]:
        return sdump(studies.add_run(sid, req.run))

    @app.delete(f"{p}/studies/{{sid}}/runs/{{run}}")
    def study_remove_run(sid: str, run: str) -> dict[str, Any]:
        return sdump(studies.remove_run(sid, run))

    @app.post(f"{p}/studies/{{sid}}/notes")
    def study_add_note(sid: str, req: TextBody) -> dict[str, Any]:
        return sdump(studies.add_note(sid, req.text))

    @app.delete(f"{p}/studies/{{sid}}/notes/{{nid}}")
    def study_delete_note(sid: str, nid: str) -> dict[str, Any]:
        return sdump(studies.delete_note(sid, nid))

    @app.put(f"{p}/studies/{{sid}}/pins/{{fid}}")
    def study_pin(sid: str, fid: str, pinned: bool = True) -> dict[str, Any]:
        return sdump(studies.pin_finding(sid, fid, pinned))

    @app.post(f"{p}/studies/{{sid}}/investigations")
    def study_attach_investigation(sid: str, req: AttachInvestigation) -> dict[str, Any]:
        q = ""
        with contextlib.suppress(ForensicError):
            q = str(container.docs.get_raw("investigation", req.investigation_id).get("question", ""))
        return sdump(studies.attach_investigation(sid, req.investigation_id, q))

    @app.post(f"{p}/studies/{{sid}}/hypotheses")
    def study_add_hypothesis(sid: str, req: HypothesisCreate) -> dict[str, Any]:
        return sdump(
            studies.add_hypothesis(
                sid, req.statement, req.rationale, req.proposed_change, req.evidence, req.source, req.reference_run
            )
        )

    @app.patch(f"{p}/studies/{{sid}}/hypotheses/{{hid}}")
    def study_patch_hypothesis(sid: str, hid: str, req: HypothesisPatch) -> dict[str, Any]:
        return sdump(
            studies.update_hypothesis(
                sid,
                hid,
                statement=req.statement,
                status=req.status,
                rationale=req.rationale,
                proposed_change=req.proposed_change,
                verdict=req.verdict,
                test_run=req.test_run or None,
                reference_run=req.reference_run or None,
                clear_test_run=req.test_run == "",
            )
        )

    @app.delete(f"{p}/studies/{{sid}}/hypotheses/{{hid}}")
    def study_delete_hypothesis(sid: str, hid: str) -> dict[str, Any]:
        return sdump(studies.delete_hypothesis(sid, hid))

    @app.post(f"{p}/studies/{{sid}}/hypotheses/{{hid}}/evidence")
    def study_add_evidence(sid: str, hid: str, link: EvidenceLink) -> dict[str, Any]:
        return sdump(studies.add_evidence(sid, hid, link))

    @app.delete(f"{p}/studies/{{sid}}/hypotheses/{{hid}}/evidence")
    def study_remove_evidence(sid: str, hid: str, kind: str, ref: str) -> dict[str, Any]:
        return sdump(studies.remove_evidence(sid, hid, kind, ref))

    @app.get(f"{p}/studies/{{sid}}/hypotheses/{{hid}}/verify")
    def study_verify(sid: str, hid: str) -> dict[str, Any]:
        h, cmp, suggestion = studies.verify(sid, hid)
        return {
            "hypothesis": h.model_dump(mode="json"),
            "comparison": cmp.model_dump(mode="json"),
            "suggestion": suggestion,
        }

    @app.get(f"{p}/studies/{{sid}}/evidence")
    def study_evidence(sid: str) -> dict[str, Any]:
        """Everything the Study page shows at once: per-run metrics vs baseline, config diffs, stored findings."""
        st = studies.get(sid)
        base = st.baseline
        rows = []
        for r in st.runs:
            run = svc.get_run(r)
            summary = {}
            with contextlib.suppress(ForensicError):
                summary = {str(sr.key): sr.aggregate() for sr in svc.series(r) if sr.key.split.value in ("val", "test")}
            deltas, changes = [], []
            if base and r != base:
                with contextlib.suppress(ForensicError):
                    cmp = svc.compare(base, r)
                    deltas = [d.model_dump(mode="json") for d in cmp.deltas]
                    changes = [c.model_dump(mode="json") for c in cmp.config_diff.meaningful]
            rows.append(
                {
                    "run": run.model_dump(mode="json", exclude={"backend_extras"}),
                    "summary": summary,
                    "deltas": deltas,
                    "config_changes": changes,
                    "is_baseline": r == base,
                    "findings": [f.model_dump(mode="json") for f in svc.findings.for_runs([r])],
                }
            )
        return {"study": sdump(st), "runs": rows}

    @app.get(f"{p}/studies/{{sid}}/export", response_class=PlainTextResponse)
    def study_export(sid: str) -> str:
        return studies.to_markdown(sid)

    @app.get(f"{p}/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return {
            "telemetry": telemetry.snapshot(),
            "index": getattr(svc.index, "stats", dict)(),
            "artifact_cache_bytes": svc.artifact_store.usage_bytes(),
            "audit": container.docs.audit_log(50),
            "notices": container.settings.notices,
        }

    web = _web_dist()
    if web is not None:
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> Any:
            if full_path.startswith("api/"):
                raise HTTPException(404)
            candidate = (web / full_path).resolve()
            if full_path and candidate.is_file() and web.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(web / "index.html")

    return app


def _diagnosis(err: str | None) -> dict[str, Any] | None:
    if not err:
        return None
    from model_forensic.infrastructure.connectors.azureml.diagnose import classify

    return classify(err).to_dict()


def _web_dist() -> Path | None:
    import os

    env = os.environ.get("MF_WEB_DIST")
    candidates = [Path(env)] if env else []
    candidates += [Path(__file__).resolve().parents[4] / "web" / "dist", Path(__file__).resolve().parent / "static"]
    return next((c for c in candidates if (c / "index.html").is_file()), None)
