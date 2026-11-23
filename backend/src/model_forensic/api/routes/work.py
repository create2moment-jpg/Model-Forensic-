"""HTTP routes: /drafts, /jobs, /ledger, /ledger.md, /work."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Response

from model_forensic.api.routes.context import RouteContext
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.shared import identity


def register(app: FastAPI, ctx: RouteContext) -> None:
    container: Container = ctx.container
    drafts = ctx.drafts
    jobs = ctx.jobs
    p = ctx.p
    studies = ctx.studies
    svc: ForensicService = ctx.svc

    @app.get(f"{p}/drafts")
    def list_drafts(kind: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Your drafts (newest first) — summaries only; open one to get its payload."""
        items, total = drafts.list(identity.current().id, kind, max(1, min(limit, 200)))
        return {"drafts": items, "total": total}

    @app.get(f"{p}/drafts/{{draft_id}}")
    def get_draft(draft_id: str) -> dict[str, Any]:
        return drafts.get(draft_id, identity.current().id)

    @app.put(f"{p}/drafts/{{draft_id}}")
    def save_draft(draft_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Autosave: create or update a draft (``kind``, ``title``, ``route`` to resume it, ``payload``)."""
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(422, "payload must be an object")
        d = drafts.save(
            draft_id,
            identity.current().id,
            str(body.get("kind", "other")),
            str(body.get("title", "")),
            str(body.get("route", "/")),
            payload,
        )
        return {k: v for k, v in d.items() if k != "payload"}

    @app.delete(f"{p}/drafts/{{draft_id}}")
    def delete_draft(draft_id: str) -> dict[str, Any]:
        drafts.delete(draft_id, identity.current().id)
        return {"deleted": draft_id}

    from model_forensic.application.ledger import LedgerService

    ledger = LedgerService(container.docs, svc)

    @app.get(f"{p}/ledger")
    def ledger_list(status: str | None = None, text: str | None = None, limit: int = 100) -> dict[str, Any]:
        """The experiment ledger (newest first): summaries with a one-line headline per entry."""
        items, total = ledger.find(status, text, max(1, min(limit, 1000)))
        return {"entries": items, "total": total}

    @app.post(f"{p}/ledger")
    def ledger_create(body: dict[str, Any]) -> dict[str, Any]:
        """New entry (title, hypothesis, change, baseline, candidates, tags, study …); with a baseline and candidates
        the outcome is computed at once (``analyse: false`` to skip)."""
        analyse = bool(body.pop("analyse", True))
        e = ledger.create(identity.current().id, body, analyse)
        container.docs.audit(identity.current().id, "ledger.create", {"id": e["id"]})
        return e

    @app.get(f"{p}/ledger.md")
    def ledger_markdown(ids: str | None = None) -> Response:
        """The ledger (or the entries in ``ids``, comma-separated) as Markdown."""
        return Response(ledger.markdown(ids.split(",") if ids else None), media_type="text/markdown")

    @app.get(f"{p}/ledger/{{entry_id}}")
    def ledger_get(entry_id: str) -> dict[str, Any]:
        return ledger.get(entry_id)

    @app.patch(f"{p}/ledger/{{entry_id}}")
    def ledger_update(entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Edit the human part (hypothesis, decision, notes …); a new baseline / candidate list re-computes the outcome."""
        return ledger.update(entry_id, identity.current().id, body)

    @app.post(f"{p}/ledger/{{entry_id}}/refresh")
    def ledger_refresh(entry_id: str) -> dict[str, Any]:
        """Re-compute the outcome (e.g. after the candidate finished training)."""
        return ledger.refresh(entry_id, identity.current().id)

    @app.delete(f"{p}/ledger/{{entry_id}}")
    def ledger_delete(entry_id: str) -> dict[str, Any]:
        ledger.delete(entry_id)
        container.docs.audit(identity.current().id, "ledger.delete", {"id": entry_id})
        return {"deleted": entry_id}

    @app.post(f"{p}/ledger/{{entry_id}}/promote")
    def ledger_promote(entry_id: str) -> dict[str, Any]:
        """Turn a decided entry into a *proposed* knowledge entry (accept it in Knowledge)."""
        added, errors = container.knowledge.import_text(ledger.knowledge_yaml(entry_id), via=f"ledger:{entry_id}")
        if errors and not added:
            raise HTTPException(422, "; ".join(errors))
        return {"added": [e.id for e in added], "errors": errors}

    @app.get(f"{p}/work")
    def work_in_progress() -> dict[str, Any]:
        """Continue where you left off: your drafts, open studies with their next step, jobs that did not finish,
        knowledge waiting for review."""
        me = identity.current().id
        mine, n_drafts = drafts.list(me, limit=10)
        open_st, n_open = studies.summaries("open", None, 10, 0)
        for st in open_st:
            hyp = st.get("hypotheses") or {}
            st["next"] = (
                "measurement checks block the verdict — fix or override them"
                if st.get("gate_blocking")
                else "record a decision"
                if hyp.get("supported") or hyp.get("rejected")
                else "verify the open hypotheses"
                if hyp.get("proposed")
                else "add hypotheses or compare runs"
            )
        unfinished = [
            {
                "id": j.id,
                "kind": j.kind,
                "title": j.title,
                "status": j.status,
                "error": j.error,
                "created_at": j.created_at,
                "rerunnable": j.kind in ("download", "book", "sync"),
            }
            for j in sorted(jobs.values(), key=lambda j: -j.created_at)
            if j.status in ("interrupted", "failed", "cancelled")
        ][:10]
        try:
            entries, _ = container.knowledge.load()
            proposed = sum(1 for e in entries if e.status == "proposed")
        except Exception:  # the overview never fails on a knowledge file
            proposed = 0
        return {
            "drafts": mine,
            "drafts_total": n_drafts,
            "studies": open_st,
            "studies_open": n_open,
            "jobs": unfinished,
            "knowledge_proposed": proposed,
        }

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
                "rerunnable": j.kind in ("download", "book", "sync"),
                "run": j.params.get("run"),  # the run it works on (warm-up state in the Runs table comes from here)
            }
            for j in sorted(jobs.values(), key=lambda j: -j.created_at)[:50]
        ]
