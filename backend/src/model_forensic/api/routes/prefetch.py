"""HTTP routes: /checks, /plots, /prefetch, /test-space."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from model_forensic.api.routes.context import RouteContext
from model_forensic.application.service import ForensicService
from model_forensic.container import Container
from model_forensic.shared import identity


def register(app: FastAPI, ctx: RouteContext) -> None:
    container: Container = ctx.container
    p = ctx.p
    prefetcher = ctx.prefetcher
    svc: ForensicService = ctx.svc

    @app.get(f"{p}/prefetch")
    def prefetch_tasks() -> dict[str, Any]:
        """Artifact prefetch tasks — running first, then planned, stopped (resumable), failed, completed."""
        return {"tasks": prefetcher.list()}

    @app.post(f"{p}/prefetch/plan")
    def prefetch_plan(body: dict[str, Any]) -> dict[str, Any]:
        """Stage 1: every file with its size, a pre-selection, what is reused / resumes / is left, time, space, risks."""
        run = str(body.get("run") or "")
        if not run:
            raise HTTPException(422, "run is required")
        t = prefetcher.plan(
            run,
            str(body.get("profile") or "fast_debug"),
            [str(x) for x in body.get("stages") or []] or None,
            [str(x) for x in body.get("paths") or []] or None,
        )
        container.docs.audit(identity.current().id, "prefetch.plan", {"run": run, "task": t["id"]})
        return t

    @app.get(f"{p}/prefetch/{{tid}}")
    def prefetch_task(tid: str) -> dict[str, Any]:
        return prefetcher.view(tid)

    @app.get(f"{p}/prefetch/{{tid}}/events")
    def prefetch_events(tid: str, after: int = 0) -> dict[str, Any]:
        """Structured events since ``after`` (phase, file, retry, stall, recovered, info, error) — a live log."""
        return prefetcher.events(tid, max(0, after))

    @app.put(f"{p}/prefetch/{{tid}}/selection")
    def prefetch_selection(tid: str, body: dict[str, Any]) -> dict[str, Any]:
        """``{"only": [keys]}`` replaces the selection; ``{"keys": [...], "selected": bool}`` changes some files."""
        if "only" in body:
            return prefetcher.select(tid, only=[str(k) for k in body.get("only") or []])
        return prefetcher.select(tid, [str(k) for k in body.get("keys") or []], bool(body.get("selected", True)))

    @app.post(f"{p}/prefetch/{{tid}}/start")
    def prefetch_start(tid: str) -> dict[str, Any]:
        """Stage 2 (or resume): download the selection; finished files are re-validated and reused."""
        container.docs.audit(identity.current().id, "prefetch.start", {"task": tid})
        return prefetcher.start(tid)

    @app.post(f"{p}/prefetch/{{tid}}/stop")
    def prefetch_stop(tid: str) -> dict[str, Any]:
        """Stop and resume later: finished files and partial downloads are kept."""
        return prefetcher.stop(tid)

    @app.post(f"{p}/prefetch/{{tid}}/cancel")
    def prefetch_cancel(tid: str) -> dict[str, Any]:
        """Cancel: not resumable; partial downloads are removed (finished files stay in the cache)."""
        return prefetcher.stop(tid, cancel=True)

    @app.delete(f"{p}/prefetch/{{tid}}")
    def prefetch_delete(tid: str) -> dict[str, Any]:
        prefetcher.delete(tid)
        return {"deleted": tid}

    from pathlib import Path as _Path

    from model_forensic.application.test_space import TestSpace
    from model_forensic.shared.paths import mf_home

    test_space = TestSpace(
        svc, container.tasks, container.docs, mf_home(), _Path(__file__).parents[1] / "defaults" / "tasks.yaml"
    )
    container.test_space = test_space

    @app.get(f"{p}/test-space")
    def test_space_home() -> dict[str, Any]:
        """Settings, production timings (p50 / p90 / p99 cold and warm per task kind), experiments and proposals."""
        return {
            "settings": test_space.settings(),
            "production": test_space.production(),
            "experiments": test_space.list(),
            "proposals": test_space.proposals(),
            "hypotheses": ["cost_class", "warm_speedup", "under_ms"],
        }

    @app.post(f"{p}/test-space/experiments")
    def test_space_start(body: dict[str, Any]) -> dict[str, Any]:
        """Measure a task kind on sample runs, cold and warm, ``repetitions`` times (optionally testing a hypothesis)."""
        kind = str(body.get("kind") or "")
        runs = [str(r) for r in body.get("runs") or []] or None
        return test_space.start(kind, runs, body.get("repetitions"), body.get("hypothesis"))

    @app.get(f"{p}/test-space/experiments/{{eid}}")
    def test_space_experiment(eid: str) -> dict[str, Any]:
        return test_space.get(eid)

    @app.post(f"{p}/test-space/experiments/{{eid}}/propose")
    def test_space_propose(eid: str) -> dict[str, Any]:
        """A versioned proposal from the experiment's evidence (config/test-space/proposals/)."""
        doc = test_space.propose(eid, identity.current().id)
        container.docs.audit(identity.current().id, "test_space.propose", {"proposal": doc["id"]})
        return doc

    @app.post(f"{p}/test-space/proposals/{{pid}}/{{decision}}")
    def test_space_review(pid: str, decision: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Accept (writes config/tasks.local.yaml, reloads task costs) or reject — recorded either way."""
        if decision not in ("accept", "reject"):
            raise HTTPException(422, "decision is accept or reject")
        doc = test_space.review(pid, decision == "accept", identity.current().id, str((body or {}).get("note", "")))
        container.docs.audit(identity.current().id, f"test_space.{decision}", {"proposal": pid})
        return doc

    @app.get(f"{p}/checks")
    def config_checks() -> dict[str, Any]:
        """Configuration checks: id, severity, current vs expected, remediation, repair prompt, validation command."""
        from model_forensic.application.config_checks import run, summary

        return summary(run(container))

    @app.get(f"{p}/plots/schema")
    def plot_schema() -> dict[str, Any]:
        """The JSON Schema every plot spec follows (v1) — also what the plotter prompt gives the LLM."""
        from model_forensic.domain.plotspec import PlotSpecV1

        return PlotSpecV1.model_json_schema()

    @app.post(f"{p}/plots/validate")
    def plot_validate(body: dict[str, Any]) -> dict[str, Any]:
        """Schema + grounding check (runs and keys exist, the scale fits the data). Invalid specs are never drawn."""
        from model_forensic.application.plotting import validate

        spec, errors, warnings = validate(body, svc)
        return {
            "ok": spec is not None,
            "errors": errors,
            "warnings": warnings,
            "spec": spec.model_dump() if spec else None,
        }

    @app.post(f"{p}/plots/render")
    def plot_render(body: dict[str, Any]) -> dict[str, Any]:
        """Validated, transformed, downsampled data for one spec (422 with the errors when it does not validate)."""
        from model_forensic.application.plotting import render, validate

        spec, errors, warnings = validate(body, svc)
        if spec is None:
            raise HTTPException(422, {"errors": errors})
        return {**render(spec, svc), "warnings": warnings}
