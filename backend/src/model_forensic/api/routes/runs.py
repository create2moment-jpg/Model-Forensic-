"""HTTP routes: /compare, /experiments, /metric-keys, /projects, /run-info, /runs, /tasks."""

from __future__ import annotations

import math
import threading
import time
import uuid
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Response

from model_forensic.api.common import Job
from model_forensic.api.routes.context import RouteContext
from model_forensic.application.knowledge import annotate
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.domain.metrics import MetricKey
from model_forensic.shared import identity
from model_forensic.shared.downsample import m4_indices


def register(app: FastAPI, ctx: RouteContext) -> None:
    _knowledge_for = ctx._knowledge_for
    container: Container = ctx.container
    jobs = ctx.jobs
    p = ctx.p
    prefetcher = ctx.prefetcher
    svc: ForensicService = ctx.svc

    def report(*a: Any, **k: Any) -> Any:  # defined in a later area
        return ctx.report(*a, **k)

    @app.get(f"{p}/runs/{{run}}/plots/suggest")
    def plot_suggest(run: str, stage: str | None = None) -> dict[str, Any]:
        """Rule-based plots for this run (from the shape of its metric keys); ``stage``: of that pipeline stage."""
        from model_forensic.application.plotting import suggest

        if stage and stage not in [n for n, _ in svc.stages(run)]:
            raise HTTPException(400, f"{run} has no stage {stage!r}")
        specs = suggest(svc, run, stage or None)
        note = ""
        if not specs and any(len(x) for x in svc.stage_series(run, stage or None)):
            note = ("This view logged single values only (an evaluation stage): they are compared as values per "
                    "stage in Metrics, not drawn as curves.")  # fmt: skip
        return {"specs": [s.model_dump() for s in specs], "stage": stage, "note": note}

    @app.post(f"{p}/runs/{{run}}/plots/ai")
    def plot_ai(run: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """The local LLM proposes plot specs for a question; each one is validated (grounded) or dropped with why."""
        from model_forensic.application.plotting import suggest_ai

        return suggest_ai(container, run, str((body or {}).get("question", "")))

    @app.get(f"{p}/runs/{{run}}/uncertainty")
    def run_uncertainty(run: str, baseline: str | None = None, stage: str | None = None) -> dict[str, Any]:
        """Uncertainty-based loss weighting: per head log σ, weight share (and over time), drift, bound, optimum gap,
        status, balance; with ``baseline`` each head's share against the baseline's."""
        from model_forensic.application.uncertainty import report

        if stage and stage not in [n for n, _ in svc.stages(run)]:
            raise HTTPException(400, f"{run} has no stage {stage!r}")
        return report(svc, run, baseline, stage or None)

    @app.get(f"{p}/runs/{{run}}/setup-clues")
    def run_setup_clues(run: str) -> dict[str, Any]:
        """How the run was set up — optimizer, LR, batch, schedule, precision, … — each with its source."""
        from model_forensic.application.setup_clues import clues

        return clues(svc, run)

    @app.post(f"{p}/runs/metric-columns")
    def metric_columns(body: dict[str, Any]) -> dict[str, Any]:
        """Which metric columns the given runs have (ranked, project headline first), and why some are empty (MC-1)."""
        from model_forensic.application.metric_columns import columns

        ids = [str(x) for x in (body.get("runs") or [])][:2000]
        runs = [r for r in (svc.index.get(svc.ref(i)) for i in ids) if r is not None]
        remote = {n: bool(getattr(s, "remote", True)) for n, s in svc.sources.items()}
        return columns(runs, list(svc.projects), remote)

    @app.post(f"{p}/runs/load-metrics")
    def load_metrics_many(body: dict[str, Any]) -> dict[str, Any]:
        """Load the metric histories of these runs in the background (``run.metrics`` tasks: bounded, deduplicated,
        visible in the task indicator, cached in memory and — for finished remote runs — on disk)."""
        from model_forensic.application.run_loader import load

        ids = [str(x) for x in (body.get("runs") or [])][:500]
        if not ids:
            raise HTTPException(422, "runs is required")
        out = []
        for rid in ids:
            out += [
                r.view() for r in load(svc, container.tasks, rid, "light", ["run.metrics"]) if r.kind == "run.metrics"
            ]
        return {"tasks": out}

    @app.post(f"{p}/runs/summaries")
    def fill_summaries(body: dict[str, Any]) -> dict[str, Any]:
        """Fill metric columns: load the metric histories of these runs in the background (``run.summary`` tasks)."""
        from model_forensic.application.metric_columns import fill

        ids = [str(x) for x in (body.get("runs") or [])]
        if not ids:
            raise HTTPException(422, "runs is required")
        return {"tasks": [r.view() for r in fill(svc, container.tasks, ids)]}

    def _view(run: str, stage: str | None = None) -> dict[str, Any]:
        """A run's series as one stage sees them (default: each metric from its last stage) — stages never mixed."""

        if stage and stage not in [n for n, _ in svc.stages(run)]:
            raise HTTPException(400, f"{run} has no stage {stage!r}")
        return {str(s.key): s for s in svc.stage_series(run, stage or None)}

    @app.get(f"{p}/runs/{{run}}/dossier")
    def run_dossier(
        run: str, baseline: str | None = None, stage: str | None = None, detail: str = "standard", save: bool = False
    ) -> Response:
        """The analysis dossier (Markdown) for an interpreting LLM; ``save`` also writes it to <inbox>/dossiers/."""
        from model_forensic.application import dossier as ds

        try:
            text = ds.build(svc, container.knowledge, container.docs, run, baseline, stage or None, detail)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        headers = {}
        if save:
            path = ds.save(text, container.knowledge.folder, run)
            headers["X-Dossier-Path"] = str(path)
            container.docs.audit(identity.current().id, "dossier.save", {"run": run, "path": str(path)})
        return Response(text, media_type="text/markdown", headers=headers)

    @app.get(f"{p}/runs/{{run}}/expectations")
    def run_expectations(run: str, stage: str | None = None) -> dict[str, Any]:
        """Expected vs actual losses: the codebase's expectations file for this run, checked; or how to produce it."""
        from model_forensic.application.expectations import evaluate

        return evaluate(svc, container.knowledge.folder, run, series=_view(run, stage))

    @app.get(f"{p}/runs/{{run}}/verdict")
    def run_verdict(
        run: str, baseline: str | None = None, stage: str | None = None, cached_only: bool = False
    ) -> dict[str, Any]:
        """The run in one sentence, three numbers and one next step (baseline picked for you, with the reason);
        ``cached_only``: answer only from what is loaded (never a backend call for metric histories)."""
        from model_forensic.application.verdict import verdict

        if stage and stage not in [n for n, _ in svc.stages(run)]:
            raise HTTPException(400, f"{run} has no stage {stage!r}")
        return verdict(svc, container.knowledge.folder, run, baseline, stage or None, cached_only)

    @app.get(f"{p}/runs/{{run}}/observations")
    def run_observations(run: str, baseline: str | None = None, stage: str | None = None) -> dict[str, Any]:
        """Every observation about a run (health, the codebase, internals, drift vs the baseline, findings) in one
        shape (design review D4), problems first."""
        from model_forensic.application import observations as om

        if stage and stage not in [n for n, _ in svc.stages(run)]:
            raise HTTPException(400, f"{run} has no stage {stage!r}")
        obs = om.collect(svc, container.knowledge.folder, run, baseline, stage or None)
        return {"observations": [o.model_dump() for o in obs], "counts": om.counts(obs), "stage": stage}

    @app.get(f"{p}/compare/loss-drift")
    def compare_loss_drift(baseline: str, candidate: str, stage: str | None = None) -> dict[str, Any]:
        """How every shared loss progresses in the candidate vs the baseline: divergence point, outcome, shape, and
        what the pattern says about the training."""
        from model_forensic.analysis.loss_drift import loss_drift

        b, c = svc.ref(baseline), svc.ref(candidate)
        b_stage = (
            stage if stage and stage in [n for n, _ in svc.stages(b)] else None
        )  # same stage in both, if it has it
        return loss_drift(
            _view(str(b), b_stage),
            _view(str(c), stage),
            ref_lr=svc.lr_series(b, b_stage),
            cand_lr=svc.lr_series(c, stage or None),
            changes=tuple(svc.diff_configs(b, c).changes),
        )

    @app.get(f"{p}/runs/{{run}}/internals")
    def run_internals(run: str, baseline: str | None = None, stage: str | None = None) -> dict[str, Any]:
        """Model internals from the kit: cross-attention per layer × modality, query statistics, notes."""
        from model_forensic.analysis.internals import internals

        base = _view(baseline) if baseline and baseline != run else None
        return internals(_view(run, stage), base)

    @app.get(f"{p}/runs/{{run}}/loss-dynamics")
    def run_loss_dynamics(run: str, baseline: str | None = None, stage: str | None = None) -> dict[str, Any]:
        """Loss dynamics: what the LR schedule did vs what the model learned (per loss, fitted), gaps to the baseline
        that are only the schedule, and the training rate of each loss term within its group."""
        from model_forensic.analysis.loss_dynamics import report

        if stage and stage not in [n for n, _ in svc.stages(run)]:
            raise HTTPException(400, f"{run} has no stage {stage!r}")
        base = None
        b_lr = None
        if baseline and baseline != run:
            b = svc.ref(baseline)
            b_stage = stage if stage and stage in [n for n, _ in svc.stages(b)] else None
            base, b_lr = _view(str(b), b_stage), svc.lr_series(b, b_stage)
        return {**report(_view(run, stage), svc.lr_series(run, stage or None), base, b_lr), "stage": stage}

    @app.get(f"{p}/runs/{{run}}/loss-questions")
    def run_loss_questions(run: str, baseline: str | None = None, stage: str | None = None) -> dict[str, Any]:
        """Training health questions (is the loss diverging? the layer refinement? …), each answered from the series
        of one stage (default: each metric from the last stage that logged it)."""
        from model_forensic.analysis.loss_questions import ask

        base = _view(baseline) if baseline and baseline != run else None
        return {"questions": [a.view() for a in ask(_view(run, stage), base)], "stage": stage}

    @app.get(f"{p}/runs/{{run}}/metric-catalog")
    def run_metric_catalog(run: str) -> dict[str, Any]:
        """The run's metrics as ``stage / metric`` (pipelines: per stage, in pipeline order) for the Metrics tab."""
        from model_forensic.application.stages import metric_catalog

        return metric_catalog(svc.series(run), [n for n, _ in svc.stages(run)])

    @app.get(f"{p}/runs/{{run}}/light")
    def run_light(run: str) -> dict[str, Any]:
        """Light Mode: the run's cheap facts at once (index + caches only) — the shell renders from this."""
        from model_forensic.application.run_loader import light

        return light(svc, run)

    @app.post(f"{p}/runs/{{run}}/load")
    def run_load(
        run: str, mode: str = "light", kinds: list[str] = Query(default=[]), baseline: str | None = None
    ) -> dict[str, Any]:
        """Start the run's background tasks (Light Mode: the light ones; Full: all; or the ``kinds`` a view needs).
        Running or cached tasks are joined, never repeated."""
        from model_forensic.application.run_loader import RUN_KINDS, load

        if mode not in ("light", "full"):
            raise HTTPException(422, "mode is light or full")
        bad = [k for k in kinds if k not in RUN_KINDS]
        if bad:
            raise HTTPException(422, f"unknown task kind(s): {', '.join(bad)}")
        recs = load(svc, container.tasks, run, mode, kinds or None, baseline)
        return {"tasks": [r.view() for r in recs]}

    @app.get(f"{p}/runs/{{run}}/tasks")
    def run_tasks(run: str) -> dict[str, Any]:
        """The authoritative state of this run's tasks (status, elapsed, cache, errors, why heavy)."""
        return {"tasks": container.tasks.list(subject=str(svc.ref(run)))}

    @app.get(f"{p}/runs/{{run}}/overview-cached")
    def run_overview_cached(run: str, baseline: str | None = None) -> dict[str, Any]:
        """The overview from what is loaded so far (never a backend call); ``ready`` says which parts are there."""
        from model_forensic.application.headline import headline

        o = svc.overview_cached(run, baseline)
        groups = headline(o["summary"], svc.team.overview, svc.team.deprioritized_classes) if o["summary"] else None
        return {
            "run": o["run"].model_dump(mode="json"),
            "ready": o["ready"],
            "metric_keys": o["metric_keys"],
            "summary": o["summary"],
            # combined metrics, then one per class (config/team.local.yaml → overview) — once the values are loaded
            "headline_groups": groups if groups and groups["keys"] else None,
            "configs": [c.model_dump(mode="json") for c in o["configs"]],
            "artifacts": [a.model_dump(mode="json") for a in o["artifacts"]],
            "baseline": str(o["baseline"]) if o["baseline"] else None,
            "what_changed": o["what_changed"].model_dump(mode="json") if o["what_changed"] else None,
            "findings": annotate(
                [f.model_dump(mode="json") for f in o["findings"]],
                list(o["findings"]),
                _knowledge_for(run, cached_only=True),
            ),
            "knowledge": _knowledge_for(run, cached_only=True).to_dict(),
        }

    @app.get(f"{p}/tasks/active")
    def tasks_active() -> dict[str, Any]:
        """Everything running now — run tasks, prefetch tasks, jobs — for the global task indicator."""
        items: list[dict[str, Any]] = []
        for t in container.tasks.list(active=True):
            items.append(
                {
                    "source": "run",
                    "id": t["id"],
                    "title": t["title"],
                    "subject": t["subject"],
                    "state": t["state"],
                    "elapsed_s": t["elapsed_s"],
                    "progress": t["progress"],
                    "link": f"/test-space?exp={t['subject']}"
                    if t["kind"].startswith("test.")
                    else f"/runs/{t['subject']}",
                }
            )
        for t in prefetcher.list():
            if t["state"] in ("running", "stopping"):
                items.append(
                    {
                        "source": "prefetch",
                        "id": t["id"],
                        "title": t["title"],
                        "subject": t["run"],
                        "state": t["state"],
                        "elapsed_s": t.get("elapsed_s", 0),
                        "progress": {
                            "files_remaining": t["files_remaining"],
                            "bytes_remaining": t["bytes_remaining"],
                            "throughput_bps": t["throughput_bps"],
                        },
                        "link": f"/prefetch?task={t['id']}",
                    }
                )
        for j in jobs.values():
            if j.status == "running":
                items.append(
                    {
                        "source": "job",
                        "id": j.id,
                        "title": j.title or j.kind,
                        "subject": str(j.params.get("run", "")),
                        "state": "running",
                        "elapsed_s": round(time.time() - j.created_at, 1),
                        "progress": j.progress,
                        "kind": j.kind,
                        "params": j.params,
                        "link": "",
                    }
                )
        return {"active": items, "count": len(items)}

    @app.get(f"{p}/runs/warm")
    def warm_runs(runs: list[str] = Query(default=[])) -> dict[str, bool]:
        """Which of these runs open without waiting for the backend (metadata cached in memory or on disk)."""
        out = {}
        for r in runs[:500]:
            try:
                out[r] = svc.is_warm(r)
            except Exception:  # an unknown run is simply not prefetched
                out[r] = False
        return out

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
            "findings": annotate(
                [f.model_dump(mode="json") for f in o.findings], list(o.findings), _knowledge_for(run)
            ),
            "knowledge": _knowledge_for(run).to_dict(),
        }

    @app.post(f"{p}/runs/{{run}}/warm")
    def warm_up(run: str, force: bool = False) -> dict[str, Any]:
        """Metadata warm-up: load a run's stages, configs, metrics and file list in the background so its page opens
        fast (artifact downloads are prefetch tasks — /prefetch)."""
        r = svc.get_run(run)
        job = Job(
            id=uuid.uuid4().hex[:10],
            kind="warm-up",
            title=f"Warm-up · {r.display_name or r.name}",
            params={"run": run, "force": force},
        )
        jobs[job.id] = job

        def work() -> None:
            try:
                job.result = svc.warm_up(run, force, progress=lambda m: job.progress.update({"step": m}))
                job.status = "done"
            except Exception as exc:
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"

        threading.Thread(target=work, daemon=True).start()
        return {"job_id": job.id}

    @app.get(f"{p}/experiments")
    def experiments(backend: str | None = None, project: str | None = None) -> list[dict[str, Any]]:
        """Experiments with run counts and last activity; the ones you have runs in come first (``mine``)."""
        return svc.experiments(backend, project or None)

    @app.get(f"{p}/projects")
    def projects() -> dict[str, Any]:
        """Project profiles (config/projects/*.local.yaml): which workspaces, experiments and naming each uses."""
        from model_forensic.projects import CONTRACT_VERSION

        return {
            "contract_version": CONTRACT_VERSION,
            "projects": [
                {
                    "id": x.id,
                    "title": x.title or x.id,
                    "description": x.description,
                    "contract_version": x.contract_version,
                    "workspaces": x.backends,
                    "experiments": x.experiments,
                    "headline": x.metrics.headline,
                    "aliases": len(x.metrics.aliases) + len(x.metrics.rules),
                    "terminology": x.terminology,
                    "unavailable": [b for b in x.backends if b not in svc.sources],
                }
                for x in svc.projects
            ],
            "notes": [n for n in container.settings.notices if n.startswith("Project")],
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
