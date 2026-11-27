"""A sweep's trials, as studio's sweep view shows them: each trial's hyper-parameters and its primary metric, ranked
in the goal's direction, the best trial marked, the parameters that vary — for a table and parallel coordinates.

Deterministic, from the index (the trials are indexed as the sweep's children; their final values come from the
summary metrics, loaded on demand like the Runs table's columns). The objective comes from the sweep job
(``objective.primary_metric`` / ``objective.goal``); without one, the first headline metric the trials share and the
metric's own direction.
"""

from __future__ import annotations

from typing import Any

from model_forensic.analysis.series_ops import direction
from model_forensic.application.verdict import metric_label
from model_forensic.domain.run import RunStatus


def _num(v: str) -> float | str:
    try:
        return float(v)
    except ValueError:
        return v


def _value(summary: dict[str, float], metric: str) -> float | None:
    if metric in summary:
        return summary[metric]
    bare = metric.split("/")[-1]
    hit = next((k for k in summary if k == bare or k.endswith(f"/{bare}")), None)
    return summary[hit] if hit else None


def sweep_view(svc: Any, ref: Any) -> dict[str, Any]:
    run = svc.get_run(ref)
    trials = []
    for c in run.children:
        try:
            trials.append(svc.get_run(c))
        except Exception:  # noqa: S112 — a trial the backend no longer returns: left out, the rest still shows
            continue
    metric = run.params.get("objective.primary_metric")
    goal = run.params.get("objective.goal")
    if not metric:
        from model_forensic.application.metric_columns import columns

        metric = next(iter(columns(trials, list(svc.projects))["default"]), None)
    hib = (
        (goal == "maximize")
        if goal in ("maximize", "minimize")
        else (direction(metric, svc.metrics) if metric else None)
    )
    keys = sorted({k for t in trials for k in t.params if not k.startswith("objective.")})
    varying = [k for k in keys if len({t.params.get(k) for t in trials}) > 1]
    rows = []
    for t in trials:
        v = _value(t.summary_metrics, metric) if metric else None
        rows.append({
            "ref": str(t.ref), "name": t.display_name or t.name, "status": t.status.value,
            "params": {k: _num(t.params[k]) for k in keys if k in t.params}, "value": v,
            "run_s": t.duration_s,
        })  # fmt: skip
    scored = [r for r in rows if r["value"] is not None]
    if hib is not None:
        scored.sort(key=lambda r: r["value"], reverse=hib)
    best = scored[0]["ref"] if scored and hib is not None else None
    order = {r["ref"]: i for i, r in enumerate(scored)}
    rows.sort(key=lambda r: (order.get(r["ref"], len(order)), r["name"]))
    done = [t for t in trials if t.status == RunStatus.COMPLETED]
    return {
        "run": str(run.ref),
        "metric": metric, "label": metric_label(metric) if metric else None, "goal": goal,
        "higher_is_better": hib, "params": keys, "varying": varying, "trials": rows, "best": best,
        "counts": {"trials": len(trials), "completed": len(done),
                   "failed": sum(t.status == RunStatus.FAILED for t in trials),
                   "canceled": sum(t.status == RunStatus.CANCELED for t in trials),
                   "running": sum(t.status in (RunStatus.RUNNING, RunStatus.QUEUED) for t in trials)},
        "missing_values": [str(t.ref) for t in done if metric and _value(t.summary_metrics, metric) is None],
    }  # fmt: skip
