"""HTTP routes: /runs/{run}/log-insights, /runs/{run}/log-patterns/propose, /log-patterns — what the logs say."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from model_forensic.api.routes.context import RouteContext
from model_forensic.application.log_insights import PatternStore, insights, propose
from model_forensic.domain.log_patterns import LogPattern
from model_forensic.shared import identity


def register(app: FastAPI, ctx: RouteContext) -> None:
    p = ctx.p
    c = ctx.container

    def store() -> PatternStore:
        return PatternStore(c.docs, getattr(c.knowledge, "folder", None))

    @app.get(f"{p}/runs/{{run}}/log-insights")
    def log_insights(run: str, stage: str | None = None) -> dict[str, Any]:
        """Facts, problems, events and curves pulled out of the run's logs by the active patterns; the noteworthy
        lines no pattern explains. Incremental: a refresh reads only what the logs gained."""
        return insights(ctx.svc, store(), run, stage or None)

    @app.post(f"{p}/runs/{{run}}/log-patterns/propose")
    def log_patterns_propose(run: str, stage: str | None = None) -> dict[str, Any]:
        """Patterns for the unexplained lines — by the local LLM (each must match the line it cites) or, without one,
        written from the lines' numbers. Stored as proposed."""
        out = propose(ctx.svc, store(), c.llm, run, stage or None)
        c.docs.audit(identity.current().id, "log_patterns.propose", {"run": run, "n": len(out["proposed"])})
        return out

    @app.get(f"{p}/log-patterns")
    def log_patterns() -> dict[str, Any]:
        s = store()
        return {
            "patterns": [x.model_dump() for x in s.all()],
            "inbox": str(s.inbox / "log_patterns") if s.inbox else None,
        }

    @app.post(f"{p}/log-patterns")
    def log_pattern_create(body: dict[str, Any]) -> dict[str, Any]:
        """A pattern written by the user (active at once)."""
        pat = LogPattern.model_validate({**body, "source": "user", "status": body.get("status", "active")})
        return store().put(pat).model_dump()

    @app.put(f"{p}/log-patterns/{{pid}}")
    def log_pattern_decide(pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Accept (``status: active``), reject or reopen a pattern."""
        out = store().decide(pid, str(body.get("status", "")))
        c.docs.audit(identity.current().id, "log_patterns.decide", {"id": pid, "status": out.status})
        return out.model_dump()

    @app.delete(f"{p}/log-patterns/{{pid}}")
    def log_pattern_delete(pid: str) -> dict[str, Any]:
        store().delete(pid)
        return {"deleted": pid}
