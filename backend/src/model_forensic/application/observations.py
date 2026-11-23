"""Producers of Observations (design review D4): one per analysis, and ``collect`` for a run.

Consumers (Expert review via ``GET /runs/{run}/observations``, the dossier, the ledger outcome) read only
``domain.observation.Observation``. Ordering: problems first (bad, warn), then by source.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from model_forensic.domain.metrics import MetricSeries
from model_forensic.domain.observation import RANK, Cause, Observation

_SOURCE_ORDER = {"code": 0, "health": 1, "internals": 2, "dynamics": 3, "drift": 4, "finding": 5, "comparison": 6}

# The kind of cause a health question points at when it fires (the dossier and review show it).
_QUESTION_CAUSE: dict[str, Cause] = {
    "diverge": "optimisation", "nonfinite": "optimisation", "spikes": "optimisation", "oscillation": "optimisation",
    "grad": "optimisation", "plateau": "schedule", "convergence": "schedule", "overfit": "data",
    "term_rising": "implementation", "term_share": "config", "term_flat": "implementation",
    "layers_diverge": "implementation", "layers_saturate": "implementation", "weighting": "config",
    "term_rate": "config", "features": "data", "queries": "implementation", "attention": "implementation", "memory": "implementation",
    "base_total": "config", "base_terms": "config", "loss_vs_eval": "data",
}  # fmt: skip


def from_answers(answers: Iterable[Any], stage: str | None = None) -> list[Observation]:
    """Health questions (``analysis.loss_questions.Answer``)."""
    out = []
    for a in answers:
        src = "internals" if a.area == "internals" else "health"
        out.append(Observation(
            id=f"{src}:{a.id}", source=src, status=a.status, title=a.question, statement=a.answer,
            evidence=tuple(a.keys), cause=_QUESTION_CAUSE.get(a.id, "unknown") if a.status in ("bad", "warn") else "unknown",
            next_step=a.ideas[0] if a.ideas and a.status in ("bad", "warn") else "", why=a.why, look=a.look,
            measure=a.measure, param=a.param, stage=stage,
        ))  # fmt: skip
    return out


def from_drift(drift: Mapping[str, Any], stage: str | None = None, top: int = 10) -> list[Observation]:
    """Loss drift against a baseline: the readings (info) and the drifting losses (warn when worse)."""
    out = [Observation(id=f"drift:reading:{i}", source="drift", status="info", title="What the loss drift says",
                       statement=r, stage=stage) for i, r in enumerate(drift.get("readings", []))]  # fmt: skip
    rows = [r for r in drift.get("rows", []) if r["outcome"] not in ("same", "insufficient")][:top]
    for r in rows:
        worse = r["outcome"] in ("behind", "crossed")
        out.append(Observation(
            id=f"drift:{r['key']}", source="drift", status="warn" if worse else "info",
            title=f"{r['key']} progresses differently from the baseline", statement=r["sentence"],
            evidence=(r["key"],), cause="config" if r.get("fits") else "unknown",
            next_step=(f"Config changes that fit the timing: {'; '.join(r['fits'][:2])}" if r.get("fits") else r.get("lr_note", "")),
            stage=stage,
        ))  # fmt: skip
    return out


def from_dynamics(rep: Mapping[str, Any], stage: str | None = None) -> list[Observation]:
    """Loss dynamics (``analysis.loss_dynamics.report``): what the LR schedule did, and gaps that are only the
    schedule (warn)."""
    from model_forensic.analysis.loss_dynamics import problems

    return [Observation(
        id=f"dynamics:{p['id']}", source="dynamics", status=p["status"], title=p["title"], statement=p["text"],
        evidence=(p["key"],), cause="schedule" if p["status"] == "warn" else "unknown",
        next_step=("Compare the runs on one LR schedule (same length and decay), or at matched points before the decay"
                   if p["status"] == "warn" else ""),
        why="A loss gap between runs with different LR schedules mixes the schedule's effect with the change's; the "
            "LR model (Tissue et al., 2024) separates them.",
        look="Expert review → Loss dynamics; Metrics → the loss with the learning rate.", stage=stage,
    ) for p in problems(rep)]  # fmt: skip


def from_checks(checks: Iterable[Mapping[str, Any]], stage: str | None = None) -> list[Observation]:
    """Expected-vs-actual checks (``application.expectations.check``)."""
    hints = {
        "data": "Look at the data: label coverage, targets present for every source, class filters, empty frames.",
        "implementation": "Look at the code path: is the weight applied, the normaliser, the initialisation, the total.",
        "config": "Look at the run's config against what the code needs.",
        "schedule": "Look at when things switch: warm-up, epoch-based switches, the stage config.",
        "optimisation": "Look at the optimisation: LR, warm-up, clipping, numerical stability.",
    }
    return [Observation(
        id=f"code:{c['id']}", source="code", status=c["status"], title=f"Against the codebase — {c['kind']}",
        statement=c["text"], evidence=tuple(c.get("keys", [])),
        cause=c["hypothesis"] if c["status"] in ("bad", "warn") else "unknown",
        next_step=hints.get(c["hypothesis"], "") if c["status"] in ("bad", "warn") else "",
        code_refs=tuple(c.get("code_refs", [])), stage=stage,
    ) for c in checks]  # fmt: skip


def from_internals(notes: Iterable[Mapping[str, str]], stage: str | None = None) -> list[Observation]:
    """Model-internals notes (``analysis.internals.notes``)."""
    return [Observation(id=f"internals:{n['id']}", source="internals", status=n["status"],
                        title="Model internals", statement=n["text"], cause="implementation", next_step=n["check"],
                        stage=stage) for n in notes]  # fmt: skip


def from_findings(findings: Iterable[Any]) -> list[Observation]:
    """Stored findings of the analysis plugins (``domain.findings.Finding``)."""
    sev = {"critical": "bad", "high": "bad", "medium": "warn", "low": "info", "info": "info"}
    out = []
    for f in findings:
        recs = getattr(f, "recommendations", None) or []
        rec = (
            (f"{recs[0].title} — {recs[0].expected_outcome}" if recs[0].expected_outcome else recs[0].title)
            if recs
            else ""
        )
        out.append(Observation(id=f"finding:{f.id}", source="finding", status=sev.get(f.severity.value, "info"),
                               title=f.title, statement=f.statement, next_step=rec))  # fmt: skip
    return out


def ordered(obs: Iterable[Observation]) -> list[Observation]:
    return sorted(obs, key=lambda o: (RANK[o.status], _SOURCE_ORDER.get(o.source, 9)))


def counts(obs: Sequence[Observation]) -> dict[str, int]:
    return {s: sum(o.status == s for o in obs) for s in ("bad", "warn", "info", "ok", "na")}


def collect(
    svc: Any,
    knowledge_folder: Any,
    run: str,
    baseline: str | None = None,
    stage: str | None = None,
    *,
    series: Mapping[str, MetricSeries] | None = None,
    baseline_series: Mapping[str, MetricSeries] | None = None,
    with_findings: bool = True,
) -> list[Observation]:
    """Every observation about a run (one stage view), optionally against a baseline, problems first."""
    from model_forensic.analysis.loss_drift import loss_drift
    from model_forensic.analysis.loss_questions import ask
    from model_forensic.application.expectations import evaluate
    from model_forensic.application.stages import stage_view

    r = svc.get_run(run)
    cur = dict(series) if series is not None else {str(s.key): s for s in stage_view(svc.series(r.ref), stage)}
    base = None
    if baseline and baseline != run:
        b = svc.get_run(baseline)
        b_stage = stage if stage and stage in [n for n, _ in svc.stages(b.ref)] else None
        base = dict(baseline_series) if baseline_series is not None else {
            str(s.key): s for s in stage_view(svc.series(b.ref), b_stage)}  # fmt: skip
    obs = from_answers(ask(cur, base), stage)
    if knowledge_folder is not None:
        ex = evaluate(svc, knowledge_folder, str(r.ref), series=cur)
        if ex.get("found"):
            obs += from_checks(ex["checks"], stage)
    if base is not None:
        b = svc.get_run(baseline)
        obs += from_drift(loss_drift(base, cur, ref_lr=svc.lr_series(b.ref), cand_lr=svc.lr_series(r.ref),
                                     changes=tuple(svc.diff_configs(b.ref, r.ref).changes)), stage)  # fmt: skip
    try:
        from model_forensic.analysis.loss_dynamics import report

        lr = svc.lr_series(r.ref)
        b_lr = svc.lr_series(svc.get_run(baseline).ref) if base is not None and baseline else None
        obs += from_dynamics(report(cur, lr, base, b_lr), stage)
    except Exception:  # noqa: S110 - the dynamics are an extra reading; the others stand without it
        pass
    if with_findings and hasattr(svc, "findings"):
        obs += from_findings(svc.findings.for_runs([r.ref]))
    return ordered(obs)
