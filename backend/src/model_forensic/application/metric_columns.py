"""Metric columns of the Runs table — chosen from the data, diagnosed when empty, repaired on demand.

Root cause of empty columns: a backend whose job listing carries no metric values (Azure ML) leaves
``summary_metrics`` empty until a run's histories are loaded. Nothing is hard-coded here:

* ``columns(runs, profiles)`` ranks the metric keys the visible runs actually have (evaluation splits first, then
  coverage), puts a project profile's ``metrics.headline`` first when the runs belong to it, and returns a
  diagnosis per backend: how many runs have values, why the others do not, and how to fill them.
* ``fill(svc, tm, runs)`` loads the metric histories of runs without values as ``run.summary`` tasks (bounded,
  deduplicated, visible in the global task indicator); each one records the final values in the index.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from model_forensic.application.tasks import TaskManager, TaskRecord
from model_forensic.domain.run import Run

DEFAULT_N = 3
EVAL_PREFIXES = ("val/", "test/", "val_", "test_", "eval")


def _rank(key: str, n: int) -> tuple[int, int, str]:
    evals = key.lower().startswith(EVAL_PREFIXES) or "/val" in key or "/test" in key
    loss = "loss" in key.lower()
    return (0 if evals and not loss else 1 if not loss else 2, -n, key)


def columns(
    runs: list[Run], profiles: list[Any] | None = None, remote: dict[str, bool] | None = None
) -> dict[str, Any]:
    cov: Counter[str] = Counter()
    for r in runs:
        cov.update(k for k, v in r.summary_metrics.items() if v is not None)
    ranked = sorted(cov, key=lambda k: _rank(k, cov[k]))
    headlines: list[str] = []
    backends = {r.ref.backend for r in runs}
    for p in profiles or []:
        h = getattr(getattr(p, "metrics", None), "headline", "") or ""
        if h and set(getattr(p, "backends", [])) & backends and h in cov and h not in headlines:
            headlines.append(h)
    default = (headlines + [k for k in ranked if k not in headlines])[:DEFAULT_N]
    per_backend: dict[str, dict[str, Any]] = {}
    for r in runs:
        b = per_backend.setdefault(r.ref.backend, {"runs": 0, "with_values": 0, "missing": []})
        b["runs"] += 1
        if r.summary_metrics:
            b["with_values"] += 1
        elif len(b["missing"]) < 500:
            b["missing"].append(str(r.ref))
    diagnosis: list[dict[str, Any]] = []
    for name, b in sorted(per_backend.items()):
        miss = b["runs"] - b["with_values"]
        if not miss:
            continue
        is_remote = (remote or {}).get(name, True)
        diagnosis.append(
            {
                "check": "MC-1",
                "backend": name,
                "severity": "warning" if b["with_values"] == 0 else "info",
                "runs_without_values": miss,
                "runs": b["runs"],
                "cause": (
                    f"the {name} job listing carries no metric values; they are filled in when a run's metric "
                    "histories are loaded (opening, warm-up or Fill metric columns)"
                    if is_remote
                    else f"{miss} run(s) in {name} logged no evaluation metric (no val / test series and no train/loss)"
                ),
                "fix": "press Fill metric columns (loads the metric histories of these runs in the background)"
                if is_remote
                else "nothing to fill — check that these runs wrote metrics",
                "fillable": is_remote,
                "missing": b["missing"],
            }
        )
    return {
        "columns": [
            {"key": k, "runs": cov[k], "coverage": round(cov[k] / len(runs), 3) if runs else 0.0} for k in ranked
        ],
        "default": default,
        "headline": headlines,
        "runs": len(runs),
        "runs_with_values": sum(1 for r in runs if r.summary_metrics),
        "diagnosis": diagnosis,
    }


def fill(svc: Any, tm: TaskManager, runs: list[str], limit: int = 200) -> list[TaskRecord]:
    """Load the histories of ``runs`` that have no metric values yet, as bounded background tasks."""
    out: list[TaskRecord] = []
    for rid in runs[:limit]:
        ref = svc.ref(rid)

        def work(rec: TaskRecord, ref: Any = ref) -> dict[str, Any]:
            from model_forensic.application.run_loader import metric_progress

            s = svc.series(ref, progress=metric_progress(svc, ref, rec))
            svc.record_summary(ref, s)
            return {"series": len(s)}

        def cached(ref: Any = ref) -> bool:
            return bool(svc.is_cached("series", ref))

        out.append(tm.submit("run.summary", str(ref), work, cached=cached))
    return out
