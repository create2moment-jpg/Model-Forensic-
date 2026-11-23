"""HTTP routes: /beliefs, /docs, /harness, /knowledge."""

from __future__ import annotations

import threading
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException

from model_forensic.api.common import Job
from model_forensic.api.routes.context import RouteContext
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.shared import identity


def register(app: FastAPI, ctx: RouteContext) -> None:
    container: Container = ctx.container
    jobs = ctx.jobs
    p = ctx.p
    svc: ForensicService = ctx.svc

    @app.get(f"{p}/knowledge")
    def knowledge_list() -> dict[str, Any]:
        entries, notes = container.knowledge.load()
        origins = container.knowledge.origins
        return {
            "entries": [{**e.model_dump(), "origin": origins.get(e.id, "")} for e in entries],
            "notes": notes,
            "folder": str(container.knowledge.folder),
            "file": str(container.knowledge.main),
            "sources": len(container.knowledge.sources),
        }

    def _sources_status() -> list[dict[str, Any]]:
        _entries, notes = container.knowledge.load()
        per_entries: dict[str, int] = {}
        for origin in container.knowledge.origins.values():
            name = origin.split(":", 1)[0]
            per_entries[name] = per_entries.get(name, 0) + 1
        docs = container.docindex.counts()
        out = []
        for s in container.knowledge.sources:
            root = s.resolved()
            out.append(
                {
                    **s.model_dump(),
                    "resolved": str(root) if root else None,
                    "exists": bool(root and root.is_dir()),
                    "entries": per_entries.get(s.name, 0),
                    "docs": docs.get(s.name, 0),
                    "problems": [n for n in notes if n.startswith(s.name + ":")][:20],
                }
            )
        return out

    @app.get(f"{p}/knowledge/sources")
    def knowledge_sources() -> dict[str, Any]:
        """Where knowledge and documentation come from (inbox first), with counts and problems."""
        container.docindex.refresh_if_stale(container.knowledge.sources)
        return {"sources": _sources_status(), "inbox": str(container.knowledge.folder)}

    @app.post(f"{p}/knowledge/sources/refresh")
    def knowledge_sources_refresh() -> dict[str, Any]:
        from model_forensic.knowledge.docindex import report_dict

        reports = container.docindex.refresh(container.knowledge.sources)
        return {"reports": [report_dict(r) for r in reports], "sources": _sources_status()}

    @app.get(f"{p}/docs/search")
    def docs_search(q: str, source: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Full-text search over the documentation of every knowledge source (sections, best first)."""
        from model_forensic.knowledge.docindex import hit_dict

        container.docindex.refresh_if_stale(container.knowledge.sources)
        return {"hits": [hit_dict(h) for h in container.docindex.search(q[:300], source, limit)]}

    @app.get(f"{p}/docs/files")
    def docs_files(source: str | None = None, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        container.docindex.refresh_if_stale(container.knowledge.sources)
        return {"files": container.docindex.files(source, max(1, min(limit, 1000)), max(0, offset))}

    @app.get(f"{p}/docs/file")
    def docs_file(source: str, path: str) -> dict[str, Any]:
        try:
            title, text = container.docindex.read(container.knowledge.sources, source, path)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        codebase = next((s.codebase for s in container.knowledge.sources if s.name == source), "")
        return {"source": source, "path": path, "title": title, "text": text[:2_000_000], "codebase": codebase}

    @app.put(f"{p}/knowledge/{{eid}}")
    def knowledge_upsert(eid: str, body: dict[str, Any]) -> dict[str, Any]:
        from model_forensic.knowledge import KnowledgeEntry

        body = {k: v for k, v in body.items() if k != "origin"}  # read-only (where the entry was read from)
        body = {**body, "id": eid}
        body.setdefault("source", {})
        if isinstance(body["source"], dict):
            body["source"].setdefault("by", identity.current().id)
            body["source"].setdefault("via", "ui")
        try:
            e = KnowledgeEntry.model_validate(body)
        except Exception as exc:
            raise HTTPException(422, f"invalid entry: {str(exc)[:500]}") from exc
        container.knowledge.upsert(e)
        container.docs.audit(identity.current().id, "knowledge.upsert", {"id": eid, "status": e.status})
        return e.model_dump()

    @app.delete(f"{p}/knowledge/{{eid}}")
    def knowledge_delete(eid: str) -> dict[str, Any]:
        if not container.knowledge.delete(eid):
            raise HTTPException(404, "no such knowledge entry")
        container.docs.audit(identity.current().id, "knowledge.delete", {"id": eid})
        return {"deleted": eid}

    @app.post(f"{p}/knowledge/import")
    def knowledge_import(body: dict[str, Any]) -> dict[str, Any]:
        """YAML from the harness (or a colleague): validated, added as *proposed* — nothing applies until accepted."""
        text = str(body.get("yaml", ""))
        if len(text) > 1_000_000:
            raise HTTPException(413, "too large")
        added, errors = container.knowledge.import_text(text, via=str(body.get("via", "import"))[:80])
        container.docs.audit(identity.current().id, "knowledge.import", {"added": [e.id for e in added]})
        return {"added": [e.id for e in added], "errors": errors}

    from model_forensic.application.beliefs import BeliefService

    beliefs = BeliefService(svc, container.knowledge, container.docs)
    container.beliefs = beliefs

    @app.get(f"{p}/beliefs")
    def beliefs_list() -> dict[str, Any]:
        """Your knowledge from experience, each with its latest verdict (colour, probability, evidence counts)."""
        return {"beliefs": beliefs.overview()}

    @app.get(f"{p}/beliefs/vocabulary")
    def beliefs_vocabulary(scope: str = "", limit: int = 12) -> dict[str, Any]:
        """Config keys, metric keys and loss families seen in the scope — for building a belief's test."""
        return beliefs.vocabulary(scope[:300], max(2, min(limit, 40)))

    @app.get(f"{p}/beliefs/{{bid}}")
    def belief_detail(bid: str) -> dict[str, Any]:
        e = beliefs.entry(bid)
        from model_forensic.application.beliefs import test_hash

        latest = beliefs.latest(bid)
        return {
            "entry": e.model_dump(),
            "latest": latest,
            "stale": bool(latest and latest.get("test_hash") != test_hash(e)),
        }

    def _verify_job(ids: list[str], title: str) -> dict[str, Any]:
        job = Job(id=uuid.uuid4().hex[:10], kind="verify-beliefs", title=title, params={"ids": ids})
        jobs[job.id] = job

        def work() -> None:
            done: dict[str, Any] = {}
            try:
                for i, bid in enumerate(ids):
                    job.progress.update({"step": f"belief {i + 1}/{len(ids)}", "belief": bid})
                    try:
                        d = beliefs.verify(bid, progress=lambda m, b=bid: job.progress.update({"detail": f"{b}: {m}"}))
                        done[bid] = {"colour": d["verdict"]["colour"], "p": d["verdict"]["p"]}
                    except Exception as exc:  # one failing belief does not stop the others
                        done[bid] = {"error": f"{type(exc).__name__}: {exc}"}
                job.result = {"verified": done}
                job.status = "done"
            except Exception as exc:
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"

        threading.Thread(target=work, daemon=True).start()
        container.docs.audit(identity.current().id, "beliefs.verify", {"ids": ids})
        return {"job_id": job.id}

    @app.post(f"{p}/beliefs/{{bid}}/verify")
    def belief_verify(bid: str) -> dict[str, Any]:
        beliefs.entry(bid)
        return _verify_job([bid], f"Check belief · {bid}")

    @app.post(f"{p}/beliefs/verify-all")
    def beliefs_verify_all() -> dict[str, Any]:
        ids = [e.id for e in beliefs.beliefs() if e.status != "retired"]
        return _verify_job(ids, f"Check {len(ids)} belief(s)")

    @app.get(f"{p}/harness/tasks")
    def harness_tasks() -> dict[str, str]:
        from model_forensic.application.harness import tasks

        return {k: v.splitlines()[0].lstrip("# ") for k, v in tasks().items()}
