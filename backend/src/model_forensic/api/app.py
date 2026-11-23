"""HTTP API (FastAPI): create_app wires the shared state and registers the routes of every area
(api/routes/, in the original order). Thin: validates input, calls the application layer, maps errors."""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from model_forensic import __version__
from model_forensic.api.auth import OPEN_PATHS, Authenticator, AuthError
from model_forensic.api.common import NO_UI_PAGE, BuildInfo, Job, JobRegistry, diagnosis_for, log
from model_forensic.api.routes import compare as compare_routes
from model_forensic.api.routes import findings as findings_routes
from model_forensic.api.routes import knowledge as knowledge_routes
from model_forensic.api.routes import llm as llm_routes
from model_forensic.api.routes import prefetch as prefetch_routes
from model_forensic.api.routes import run_analysis as run_analysis_routes
from model_forensic.api.routes import runs as runs_routes
from model_forensic.api.routes import settings as settings_routes
from model_forensic.api.routes import studies as studies_routes
from model_forensic.api.routes import system as system_routes
from model_forensic.api.routes import work as work_routes
from model_forensic.container import Container
from model_forensic.observability.telemetry import telemetry
from model_forensic.shared import identity, webdist
from model_forensic.shared.errors import ForensicError

__all__ = ["BuildInfo", "Job", "create_app"]


def create_app(container: Container) -> FastAPI:
    svc = container.service
    app = FastAPI(title="Model Forensic", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    jobs = JobRegistry(container.docs)
    app.state.jobs = jobs

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
            details["diagnosis"] = diagnosis_for(f"{exc.message} {exc.__cause__ or ''}")
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

    def _pending(run: str, profile: Any) -> dict[str, Any]:
        try:
            return dict(svc.pending(run, profile))
        except Exception as exc:  # the hint must never break the view
            return {"profile": profile.value, "files": 0, "bytes": 0, "error": type(exc).__name__}

    def _knowledge_for(run: str, cached_only: bool = False) -> Any:
        """What the knowledge sets aside for a run (union over the run and its stages); never fails a page.
        ``cached_only``: match on configs only if they are loaded (the progressive overview never fetches them)."""
        from model_forensic.application.knowledge import applied, combined
        from model_forensic.knowledge import Applied

        try:
            return combined(applied(svc, container.knowledge, run, cached_only))
        except Exception as exc:
            log.warning("knowledge not applied", run=run, error=type(exc).__name__)
            return Applied()

    studies = container.studies

    def sdump(st: Any) -> dict[str, Any]:
        return st.model_dump(mode="json")  # type: ignore[no-any-return]

    ctx = SimpleNamespace(
        _knowledge_for=_knowledge_for,
        _pending=_pending,
        container=container,
        jobs=jobs,
        p=p,
        sdump=sdump,
        server=server,
        started=started,
        studies=studies,
        svc=svc,
    )
    for area in (
        system_routes,
        prefetch_routes,
        runs_routes,
        settings_routes,
        run_analysis_routes,
        work_routes,
        llm_routes,
        compare_routes,
        knowledge_routes,
        findings_routes,
        studies_routes,
    ):
        area.register(app, ctx)
    web = webdist.web_dist()
    app.state.web_dist = web
    build = BuildInfo(web)
    app.state.build = build
    ctx.build = build  # the health route (api/routes/system.py) reads it per request
    if web is None:

        @app.get("/{full_path:path}", include_in_schema=False)
        def no_ui(full_path: str) -> Any:
            """No built UI: say so (and how to fix it) instead of a bare 404."""
            from fastapi.responses import HTMLResponse

            if full_path.startswith("api/"):
                raise HTTPException(404)
            where = "\n".join(str(c) for c in webdist.web_dist_candidates())
            return HTMLResponse(NO_UI_PAGE.format(where=where), status_code=503)

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
