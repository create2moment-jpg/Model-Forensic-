"""HTTP routes: /analyses, /analyze, /feedback, /findings, /gate, /investigate, /metrics, /noise, /prompts, /reports, /runs, /studies, /study-templates."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from model_forensic.ai.tools import Permission, ToolBox
from model_forensic.api.common import AnalyzeRequest, InvestigateRequest, ReportRequest, StudyCreate
from model_forensic.api.routes.context import RouteContext
from model_forensic.application.knowledge import annotate
from model_forensic.application.ports import is_offline
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.domain.metrics import MetricKey
from model_forensic.reports.markdown import build_report, to_html
from model_forensic.shared import identity


def register(app: FastAPI, ctx: RouteContext) -> None:
    _knowledge_for = ctx._knowledge_for
    container: Container = ctx.container
    p = ctx.p
    sdump = ctx.sdump
    studies = ctx.studies
    svc: ForensicService = ctx.svc

    @app.get(f"{p}/runs/{{run}}/harness-brief", response_class=PlainTextResponse)
    def harness_brief(run: str, task: str | None = None) -> str:
        """Markdown brief for a coding agent in the training project (what the run shows + a task)."""
        from model_forensic.application.harness import brief

        container.docindex.refresh_if_stale(container.knowledge.sources)
        return brief(svc, container.knowledge, run, task, docindex=container.docindex)

    @app.get(f"{p}/runs/{{run}}/timeline")
    def run_timeline(run: str) -> dict[str, Any]:
        """The run as stages and sub-stages with durations (measured vs estimated), current stage, command,
        environment and the evidence each stage has (configs, metrics, files, logs, code diff)."""
        from model_forensic.application.timeline import timeline

        return timeline(svc, run)

    @app.get(f"{p}/runs/{{run}}/code-diff")
    def run_code_diff(run: str, stage: str | None = None) -> dict[str, Any]:
        """The code diff the job saved (raw text, ≤ 2 MB) with file / line counts — evidence, not interpretation."""
        from model_forensic.application.timeline import read_code_diff

        return read_code_diff(svc, run, stage)

    @app.get(f"{p}/runs/{{run}}/knowledge")
    def run_knowledge(run: str) -> dict[str, Any]:
        """Which knowledge applies to the run and each of its stages, and what it sets aside."""
        from model_forensic.application.knowledge import applied

        return {st or "run": a.to_dict() for st, a in applied(svc, container.knowledge, run).items()}

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
        kn = _knowledge_for(req.run)
        return [
            {
                "analysis": r.analysis,
                "skipped_reason": r.skipped_reason,
                "notes": r.notes,
                "findings": annotate([f.model_dump(mode="json") for f in r.findings], list(r.findings), kn),
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

    @app.get(f"{p}/studies")
    def list_studies(
        response: Response, status: str | None = None, q: str | None = None, limit: int = 500, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Study summaries (open first, newest first); filter by status, search title / goal / owner; paged —
        the total is in ``X-Total-Count``."""
        if status not in (None, "", "open", "closed"):
            raise HTTPException(422, "status must be open or closed")
        items, total = studies.summaries(
            status or None, (q or "").strip()[:200] or None, max(1, min(limit, 2000)), offset
        )
        response.headers["X-Total-Count"] = str(total)
        return items

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

        if is_offline(container.llm):  # no LLM: the rule-based checks, citing the reading
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

        key = str(body.get("key", ""))
        reading = _runs_reading(key, [str(x) for x in body.get("runs", [])])
        lines = [f"{key}: {x}" for x in reading.facts + reading.warnings]
        evidence = [{"id": f"E{i + 1}", "text": t} for i, t in enumerate(lines)]
        if is_offline(container.llm) or not lines:
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

        body = body or {}
        f = svc.findings.get(fid)
        run = str(body.get("run") or _finding_run(f))
        llm = None if is_offline(container.llm) else container.llm
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

    ctx.report = report
