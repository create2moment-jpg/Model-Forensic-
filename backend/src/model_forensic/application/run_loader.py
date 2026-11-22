"""Opening a run progressively: the shell from cheap facts at once, every expensive part as its own task.

``light`` answers from the index and caches only (no backend call except resolving an unknown run). ``load`` submits
the run's tasks (configs, file listing, metric histories, comparison, code diff…) to the application-scoped
:class:`~model_forensic.application.tasks.TaskManager`; Light Mode only the kinds marked ``light_mode``, Full Mode all
of them, or exactly the kinds a view asked for. A task already running or cached is joined, never repeated.
"""

from __future__ import annotations

from typing import Any

from model_forensic.application.tasks import TaskManager, TaskRecord

HEADLINE_N = 6
RUN_KINDS = ("run.stages", "run.configs", "run.artifacts", "run.metrics", "run.comparison", "run.code_diff")


def light(svc: Any, run: str) -> dict[str, Any]:
    """Run id, name, experiment, owner, status, timestamps, known stages, cached summaries — nothing fetched."""
    from model_forensic.application.metric_columns import columns

    r = svc.get_run(run)
    known_stages = [{"run": str(c)} for c in r.children]
    head = columns([r], list(getattr(svc, "projects", []) or []), n=HEADLINE_N)
    return {
        "run": r.model_dump(mode="json", exclude={"backend_extras"}),
        "id": str(r.ref),
        "stages_known": known_stages,
        "cached": {w: svc.is_cached(w, r.ref) for w in ("series", "configs", "artifacts")},
        "summary_metrics": r.summary_metrics,
        # the few values the overview shows (project headline first, then evaluation metrics) — the rest is in Metrics
        "headline": head["default"],
        "metrics_total": len(r.summary_metrics),
    }


def load(
    svc: Any,
    tm: TaskManager,
    run: str,
    mode: str = "light",
    kinds: list[str] | None = None,
    baseline: str | None = None,
) -> list[TaskRecord]:
    ref = svc.ref(run)
    subject = str(ref)
    want = set(kinds or [k for k in RUN_KINDS if mode == "full" or tm.spec(k).light_mode])
    recs: dict[str, TaskRecord] = {}

    def need(kind: str) -> TaskRecord:
        if kind in recs:
            return recs[kind]
        deps = [need(d) for d in tm.spec(kind).depends_on if d in RUN_KINDS]
        fn, cached = _work(svc, ref, kind, baseline)
        cur = tm.find(kind, subject)
        force = bool(
            kind == "run.comparison"
            and baseline
            and cur is not None
            and cur.state == "done"
            and cur.result.get("baseline") != str(svc.ref(baseline))
        )  # another baseline was picked: compare again
        recs[kind] = tm.submit(kind, subject, fn, deps, cached, force=force)
        return recs[kind]

    for k in RUN_KINDS:
        if k in want:
            need(k)
    return list(recs.values())


def metric_progress(svc: Any, ref: Any, rec: TaskRecord) -> Any:
    """A ``progress`` callback for ``svc.series``: histories done / total per part (run + pipeline stages)."""
    import time

    try:
        parts = 1 + len(svc.stages(ref))
    except Exception:
        parts = 1
    seen: dict[str, tuple[int, int]] = {}

    def cb(p: dict[str, Any]) -> None:
        part = p.get("stage") or "run"
        seen[part] = (int(p["done"]), int(p["total"]))
        done = sum(d for d, _ in seen.values())
        total = sum(t for _, t in seen.values())
        rec.progress = {
            "step": f"metric histories {done}/{total}" + (f" · part {len(seen)}/{parts} ({part})" if parts > 1 else ""),
            "done": done,
            "total": total,
            "parts_seen": len(seen),
            "parts": parts,
        }
        rec.last_activity = time.time()

    return cb


def _work(svc: Any, ref: Any, kind: str, baseline: str | None = None) -> tuple[Any, Any]:
    def stages(rec: TaskRecord) -> dict[str, Any]:
        st = svc.stages(ref)
        return {"stages": [n for n, _ in st]}

    def configs(rec: TaskRecord) -> dict[str, Any]:
        return {"snapshots": len(svc.configs(ref))}

    def artifacts(rec: TaskRecord) -> dict[str, Any]:
        arts = svc.artifacts(ref)
        return {"files": len(arts), "bytes": sum(a.size_bytes or 0 for a in arts)}

    def metrics(rec: TaskRecord) -> dict[str, Any]:
        s = svc.series(ref, progress=metric_progress(svc, ref, rec))
        svc.record_summary(ref, s)
        return {"series": len(s), "points": sum(len(x) for x in s)}

    def comparison(rec: TaskRecord) -> dict[str, Any]:
        b = svc.ref(baseline) if baseline else svc.suggest_baseline(ref)
        if b is None or b == ref:
            return {"baseline": None}
        rec.progress["step"] = f"loading the baseline {b}"
        svc.series(b)
        svc.configs(b)
        rec.progress["step"] = "comparing"
        cmp = svc.compare(b, ref)
        return {"baseline": str(b), "deltas": len(cmp.deltas)}

    def code_diff(rec: TaskRecord) -> dict[str, Any]:
        from model_forensic.application.timeline import find_code_diffs

        return {"diffs": len(find_code_diffs(svc, str(ref)))}

    table = {
        "run.stages": (stages, None),
        "run.configs": (configs, lambda: svc.is_cached("configs", ref)),
        "run.artifacts": (artifacts, lambda: svc.is_cached("artifacts", ref)),
        "run.metrics": (metrics, lambda: svc.is_cached("series", ref)),
        "run.comparison": (comparison, None),
        "run.code_diff": (code_diff, lambda: svc.is_cached("artifacts", ref)),
    }
    return table[kind]
