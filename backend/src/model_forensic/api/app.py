"""HTTP API (FastAPI). Thin: validates input, calls ForensicService, maps errors.

Read-only by default. Long operations (sync, downloads) run as background jobs with progress and
cancellation via /jobs, so the UI never blocks on slow backends.
"""

from __future__ import annotations

import contextlib
import mimetypes
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from model_forensic import __version__
from model_forensic.ai.investigator import Persona
from model_forensic.ai.tools import Permission, ToolBox
from model_forensic.container import Container
from model_forensic.domain.metrics import MetricKey
from model_forensic.domain.study import EvidenceLink, HypothesisStatus
from model_forensic.infrastructure.artifacts.store import DownloadProfile
from model_forensic.observability.telemetry import telemetry
from model_forensic.reports.markdown import build_report, to_html
from model_forensic.settings import LLMConfig
from model_forensic.shared.errors import ForensicError


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
    paths: list[str] | None = None


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
            "backends": sorted(n for n in svc.sources if n not in svc.sync_errors),
            "read_only": container.settings.read_only,
            "llm": container.llm.model,
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
            step = max(1, len(v) // max_points)
            out.append(
                {
                    "key": str(s.key),
                    "steps": st[::step].tolist(),
                    "values": [None if x != x else x for x in v[::step].tolist()],
                    "timestamps": list(s.timestamps[::step]) if s.timestamps else None,
                    "source": s.source,
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
                    "error": container.backend_errors.get(b.name) or svc.sync_errors.get(b.name),
                }
            )
        return out

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

    @app.get(f"{p}/runs/{{run}}/stages")
    def stages(run: str) -> list[dict[str, Any]]:
        """Pipeline stages (child runs) in order; empty for non-pipeline runs."""
        out = []
        for name, ref in svc.stages(run):
            try:
                child = svc.get_run(ref)
                out.append({"name": name, "ref": str(ref), "status": child.status.value, "kind": child.kind.value,
                            "duration_s": child.duration_s, "failure": child.failure.message if child.failure else None})
            except Exception as exc:
                out.append({"name": name, "ref": str(ref), "status": "unknown", "kind": "other", "duration_s": None,
                            "failure": f"{type(exc).__name__}"})
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
                files, prog = svc.download(run, req.profile, cancel=job.cancel, on_progress=progress, paths=req.paths)
                progress(prog)
                job.result = {
                    "files": [
                        {
                            "path": f.artifact.path,
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
        art = next((a for a in svc.artifacts(ref) if a.path == path), None)
        if art is None:
            raise HTTPException(404, "artifact not found")
        name = PurePosixPath(path).name.replace('"', "") or "artifact"
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        headers = {"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"}
        hit = svc.artifact_store.cached(art)
        if hit is not None:
            return FileResponse(hit.local_path, media_type=media, headers=headers)
        container.docs.audit("api", "artifact_stream", {"run": run, "path": path})
        return StreamingResponse(svc.source(ref).open_artifact(ref.run_id, path), media_type=media, headers=headers)

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
        from model_forensic.application.ports import ChatMessage

        t0 = time.perf_counter()
        try:
            r = container.llm.complete([ChatMessage("user", "Reply with the single word OK.")])
            return {
                "ok": True,
                "model": r.model,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "reply": (r.content or "")[:200],
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((time.perf_counter() - t0) * 1000),
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
            }
            for st in studies.all()
        ]

    @app.post(f"{p}/studies")
    def create_study(req: StudyCreate) -> dict[str, Any]:
        return sdump(studies.create(req.title, req.goal, req.runs, req.baseline))

    @app.get(f"{p}/studies/{{sid}}")
    def get_study(sid: str) -> dict[str, Any]:
        return sdump(studies.get(sid))

    @app.patch(f"{p}/studies/{{sid}}")
    def patch_study(sid: str, req: StudyPatch) -> dict[str, Any]:
        st = studies.update(sid, req.title, req.goal)
        if req.baseline is not None:
            st = studies.set_baseline(sid, req.baseline or None)
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


def _web_dist() -> Path | None:
    import os

    env = os.environ.get("MF_WEB_DIST")
    candidates = [Path(env)] if env else []
    candidates += [Path(__file__).resolve().parents[4] / "web" / "dist", Path(__file__).resolve().parent / "static"]
    return next((c for c in candidates if (c / "index.html").is_file()), None)
