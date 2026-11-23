"""HTTP routes: /diagnostics, /studies."""

from __future__ import annotations

import contextlib
import json
import threading
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from model_forensic.api.common import (
    AttachInvestigation,
    HypothesisCreate,
    HypothesisPatch,
    Job,
    RunBody,
    StudyPatch,
    TextBody,
)
from model_forensic.api.routes.context import RouteContext
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.domain.study import EvidenceLink
from model_forensic.observability.telemetry import telemetry
from model_forensic.shared.errors import ForensicError


def register(app: FastAPI, ctx: RouteContext) -> None:
    container: Container = ctx.container
    jobs = ctx.jobs
    p = ctx.p
    sdump = ctx.sdump
    studies = ctx.studies
    svc: ForensicService = ctx.svc

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
        job = Job(id=uuid.uuid4().hex[:10], kind="book", title=f"Study book · {sid}", params={"sid": sid, "ai": ai})
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
        path = container.settings.paths()["work"] / sid / "index.html"
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

    ctx.study_book = study_book
