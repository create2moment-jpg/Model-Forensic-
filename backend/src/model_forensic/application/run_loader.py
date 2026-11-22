"""Opening a run progressively: the shell from cheap facts at once, every expensive part as its own task.

``light`` answers from the index and caches only (no backend call except resolving an unknown run). ``load`` submits
the run's tasks (configs, file listing, metric histories, comparison, code diff…) to the application-scoped
:class:`~model_forensic.application.tasks.TaskManager`; Light Mode only the kinds marked ``light_mode``, Full Mode all
of them, or exactly the kinds a view asked for. A task already running or cached is joined, never repeated.
"""

from __future__ import annotations

from typing import Any

from model_forensic.application.tasks import TaskManager, TaskRecord

RUN_KINDS = ("run.stages", "run.configs", "run.artifacts", "run.metrics", "run.comparison", "run.code_diff")


def light(svc: Any, run: str) -> dict[str, Any]:
    """Run id, name, experiment, owner, status, timestamps, known stages, cached summaries — nothing fetched."""
    r = svc.get_run(run)
    known_stages = [{"run": str(c)} for c in r.children]
    return {
        "run": r.model_dump(mode="json", exclude={"backend_extras"}),
        "id": str(r.ref),
        "stages_known": known_stages,
        "cached": {w: svc.is_cached(w, r.ref) for w in ("series", "configs", "artifacts")},
        "summary_metrics": r.summary_metrics,
    }


def load(svc: Any, tm: TaskManager, run: str, mode: str = "light", kinds: list[str] | None = None) -> list[TaskRecord]:
    ref = svc.ref(run)
    subject = str(ref)
    want = set(kinds or [k for k in RUN_KINDS if mode == "full" or tm.spec(k).light_mode])
    recs: dict[str, TaskRecord] = {}

    def need(kind: str) -> TaskRecord:
        if kind in recs:
            return recs[kind]
        deps = [need(d) for d in tm.spec(kind).depends_on if d in RUN_KINDS]
        fn, cached = _work(svc, ref, kind)
        recs[kind] = tm.submit(kind, subject, fn, deps, cached)
        return recs[kind]

    for k in RUN_KINDS:
        if k in want:
            need(k)
    return list(recs.values())


def _work(svc: Any, ref: Any, kind: str) -> tuple[Any, Any]:
    def stages(rec: TaskRecord) -> dict[str, Any]:
        st = svc.stages(ref)
        return {"stages": [n for n, _ in st]}

    def configs(rec: TaskRecord) -> dict[str, Any]:
        return {"snapshots": len(svc.configs(ref))}

    def artifacts(rec: TaskRecord) -> dict[str, Any]:
        arts = svc.artifacts(ref)
        return {"files": len(arts), "bytes": sum(a.size_bytes or 0 for a in arts)}

    def metrics(rec: TaskRecord) -> dict[str, Any]:
        s = svc.series(ref)
        svc.record_summary(ref, s)
        return {"series": len(s), "points": sum(len(x) for x in s)}

    def comparison(rec: TaskRecord) -> dict[str, Any]:
        b = svc.suggest_baseline(ref)
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
