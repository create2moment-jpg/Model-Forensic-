"""The verdict: a run (or a comparison) answered in one sentence, three numbers and one next step.

Built only from what the analyses already produce — the observations (health questions, expected-vs-actual checks,
loss drift and dynamics, stored findings), the headline metrics (``application.headline``) and the comparison —
so it never says anything the evidence below it does not. The baseline is picked for the user, with the reason, so
"what changed" is answered instead of asked.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from model_forensic.analysis.series_ops import direction, total_loss
from model_forensic.domain.metrics import MetricSeries
from model_forensic.domain.observation import Observation

SPARK_POINTS = 32


def pick_baseline(svc: Any, ref: Any) -> tuple[Any | None, str]:
    """``(baseline ref, why)``: a run tagged ``role=baseline`` in the same experiment, else the previous completed run
    of the same kind there (never the run itself, its stages or its pipeline)."""
    r = svc.get_run(ref)
    tagged = svc.suggest_baseline(r.ref)
    if tagged is not None:
        return tagged, f"tagged role=baseline in {r.experiment}"
    if not r.experiment:
        return None, ""
    from model_forensic.application.ports import SearchFilters

    res = svc.search_filters(SearchFilters(experiment=r.experiment, statuses=("completed",)), limit=50)
    own = {str(c) for c in r.children} | ({str(r.parent)} if r.parent else set()) | {str(r.ref)}
    older = [
        x
        for x in res.runs
        if str(x.ref) not in own
        and (x.parent is None) == (r.parent is None)
        and (r.created_at is None or x.created_at is None or x.created_at < r.created_at)
    ]
    # the same kind first (a pipeline against a pipeline), then the newest
    older.sort(key=lambda x: (x.kind == r.kind, x.created_at.timestamp() if x.created_at else 0.0), reverse=True)
    if older:
        return older[0].ref, f"the previous completed run in {r.experiment}"
    return None, ""


def _last(s: MetricSeries | None) -> float | None:
    if s is None:
        return None
    _, v = s.finite()
    return float(v[-1]) if v.size else None


def _spark(s: MetricSeries) -> list[float]:
    _, v = s.finite()
    if not v.size:
        return []
    idx = sorted({int(i) for i in np.linspace(0, v.size - 1, min(v.size, SPARK_POINTS))})
    return [float(v[i]) for i in idx]


def _fmt(x: float) -> str:
    a = abs(x)
    if a == 0:
        return "0"
    if a >= 1000 or a < 0.001:
        return f"{x:.3g}"
    return f"{x:.4g}"


def _label(key: str) -> str:
    """``val/NDS`` → ``NDS``; ``val/score[class=Car]`` → ``score · Car``; ``train/loss`` → ``train loss`` (a ``task``
    dimension is left out: it names the head, not the metric)."""
    name, _, dims = key.partition("[")
    split, _, base = name.rpartition("/")
    if base == "loss" and split:
        base = f"{split.rsplit('/', 1)[-1]} loss"
    vals = [v for k, _, v in (p.partition("=") for p in dims.rstrip("]").split(",")) if v and k != "task"]
    return f"{' '.join(vals)} {base}" if vals else base


def headline_numbers(
    svc: Any, series: Mapping[str, MetricSeries], base: Mapping[str, MetricSeries] | None, n: int = 3
) -> list[dict[str, Any]]:
    """The run's headline metrics (combined evaluation metrics first, else the training loss): value, sparkline and
    the change against the baseline, with whether that change is good."""
    from model_forensic.application.headline import headline

    keys = list(headline(series.keys(), svc.team.overview, svc.team.deprioritized_classes)["combined"])[:n]
    if len(keys) < n:
        tl = total_loss(series)
        if tl and tl not in keys:
            keys.append(tl)
    out = []
    for k in keys[:n]:
        s = series.get(k)
        v = _last(s)
        if s is None or v is None:
            continue
        b = _last(base.get(k)) if base else None
        hib = direction(k, svc.metrics)
        delta = (v - b) / abs(b) if b not in (None, 0) else None
        # below the metric's meaningful change (registry): within noise — neither good nor bad
        floor = float(getattr(svc.metrics.lookup(k.split("[", 1)[0]), "min_meaningful_rel_change", 0.01) or 0.01)
        noise = delta is not None and abs(delta) < floor
        good = None if delta is None or hib is None or noise else (delta > 0) == hib
        out.append({
            "key": k, "label": _label(k), "value": v, "text": _fmt(v), "baseline": b, "delta_pct": None if delta is None else 100 * delta,
            "higher_is_better": hib, "good": good, "noise": noise, "spark": _spark(s), "points": len(s),
        })  # fmt: skip
    return out


def _first_sentence(text: str, limit: int = 180) -> str:
    t = " ".join(text.split())
    cut = t.find(". ")
    t = t[: cut + 1] if 0 < cut < limit else t
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _delta_text(h: Mapping[str, Any]) -> str:
    d = h.get("delta_pct")
    return f"{h['label']} {h['text']}" + (
        "" if d is None else f", {d:+.1f}% vs baseline" + (" (within noise)" if h.get("noise") else "")
    )


def verdict(
    svc: Any,
    knowledge_folder: Any,
    run: str,
    baseline: str | None = None,
    stage: str | None = None,
    cached_only: bool = False,
) -> dict[str, Any]:
    """``state`` (healthy | attention | problem | running | failed | not_loaded | no_data), ``sentence``, ``detail``,
    ``headline`` numbers, ``baseline`` (+ why), ``next`` step, the observation counts and the top problems."""
    from model_forensic.application import observations as om

    r = svc.get_run(run)
    b_ref, why = (svc.ref(baseline), "chosen") if baseline and baseline != run else pick_baseline(svc, r.ref)
    if b_ref is not None and str(b_ref) == str(r.ref):
        b_ref, why = None, ""
    out: dict[str, Any] = {
        "run": str(r.ref),
        "status": r.status.value,
        "stage": stage,
        "baseline": {"ref": str(b_ref), "why": why, "name": _name(svc, b_ref)} if b_ref else None,
        "headline": [],
        "next": None,
        "problems": [],
        "counts": {},
        # reading a remote run's histories is heavy (Azure ML): the page asks; a local one is read at once
        "remote": bool(getattr(svc.sources.get(r.ref.backend), "remote", True)),
    }
    out["lead"] = ""
    if r.status.value == "failed":
        msg = r.failure.message if r.failure else "the run failed"
        out |= {"state": "failed", "sentence": f"Failed — {_first_sentence(msg, 140)}", "detail": ""}
        if r.failure and r.failure.category:
            out["detail"] = f"Failure kind: {r.failure.category}."
        out["next"] = {"text": "Open the logs at the failure", "tab": "files", "view": "logs"}
        return out
    if cached_only and not svc.is_cached("series", r.ref):
        out |= {"state": "not_loaded", "sentence": "Not read yet — the verdict needs the run's metric histories.",
                "detail": ""}  # fmt: skip
        return out
    series = {str(s.key): s for s in svc.stage_series(r.ref, stage)}
    if not series:
        out |= {"state": "no_data", "sentence": "No metrics logged" + (f" in stage {stage}" if stage else "") + ".",
                "detail": ""}  # fmt: skip
        return out
    base = None
    if b_ref is not None:
        try:
            b_stage = stage if stage and stage in [n for n, _ in svc.stages(b_ref)] else None
            base = {str(s.key): s for s in svc.stage_series(b_ref, b_stage)}
        except Exception:  # the baseline cannot be read: the run is still answered on its own
            base = None
    out["headline"] = headline_numbers(svc, series, base)
    obs: list[Observation] = om.collect(
        svc, knowledge_folder, str(r.ref), str(b_ref) if base is not None else None, stage,
        series=series, baseline_series=base,
    )  # fmt: skip
    probs = [o for o in obs if o.status in ("bad", "warn")]
    bad = [o for o in probs if o.status == "bad"]
    out["counts"] = om.counts(obs)
    out["problems"] = [
        {"id": o.id, "status": o.status, "title": o.title, "statement": _first_sentence(o.statement), "keys": list(o.evidence)}
        for o in probs[:5]
    ]  # fmt: skip
    running = r.status.value == "running"
    if bad:
        state = "problem"
        head = f"{len(bad)} problem{'s' if len(bad) > 1 else ''}" + (
            f", {len(probs) - len(bad)} more to look at" if len(probs) > len(bad) else ""
        )
    elif probs:
        state = "attention"
        head = "No problems found · " + (f"{len(probs)} notes to look at" if len(probs) > 1 else "one note to look at")
    else:
        state = "healthy"
        head = "Healthy run"
    if running:
        head = f"Running · {head[0].lower()}{head[1:]}" if state != "healthy" else "Running · on track so far"
    lead = out["headline"][0] if out["headline"] else None
    out["state"] = "running" if running and state == "healthy" else state
    out["sentence"] = head + "."
    out["lead"] = _delta_text(lead) if lead else ""
    top = probs[0] if probs else None
    out["detail"] = f"{top.title}: {_first_sentence(top.statement, 160)}" if top else ""
    if top and top.next_step:
        out["next"] = {"text": _first_sentence(top.next_step, 160), "tab": "review", "keys": list(top.evidence)}
    elif base is not None and out["headline"]:
        good = [h for h in out["headline"] if h["good"] is True]
        worse = [h for h in out["headline"] if h["good"] is False]
        if good and not worse:
            out["next"] = {
                "text": "Better than the baseline on the headline metrics — compare in detail before you adopt it",
                "tab": "compare",
            }
        elif worse:
            out["next"] = {
                "text": f"{worse[0]['label']} is worse than the baseline — see what changed",
                "tab": "compare",
            }
    if out["next"] is None and b_ref is None:
        out["next"] = {"text": "Pick a baseline to see what changed", "tab": "summary"}
    return out


def _name(svc: Any, ref: Any) -> str:
    try:
        run = svc.get_run(ref)
        return str(run.display_name or run.name or ref)
    except Exception:
        return str(ref)


def compare_verdict(svc: Any, baseline: str, candidate: str, stage: str | None = None) -> dict[str, Any]:
    """A comparison in one sentence: better / worse on how many metrics (the largest moves named) and what changed."""
    cmp = svc.compare(baseline, candidate, stage=stage)
    from model_forensic.application.headline import headline

    moved = [d for d in cmp.deltas if d.relative is not None and math.isfinite(d.relative)]
    # the headline metrics are named first (NDS before a matching statistic), then the largest moves
    hk = set(headline([d.metric for d in cmp.deltas], svc.team.overview, svc.team.deprioritized_classes)["keys"])

    def order(d: Any) -> tuple[bool, float]:
        return (d.metric not in hk, -abs(d.relative or 0))

    better = sorted([d for d in moved if d.verdict.value == "improved"], key=order)
    worse = sorted([d for d in moved if d.verdict.value == "regressed"], key=order)
    judged = [d for d in cmp.deltas if d.verdict.value in ("improved", "regressed", "unchanged")]

    def named(ds: Sequence[Any]) -> str:
        return ", ".join(f"{_label(d.metric)} {100 * (d.relative or 0):+.1f}%" for d in ds[:2])

    if not judged:
        sentence = "No shared metric to compare."
        state = "no_data"
    elif better and not worse:
        sentence = f"Candidate is better on {len(better)} of {len(judged)} metrics ({named(better)}); none worse."
        state = "better"
    elif worse and not better:
        sentence = f"Candidate is worse on {len(worse)} of {len(judged)} metrics ({named(worse)}); none better."
        state = "worse"
    elif better or worse:
        sentence = (f"Mixed: better on {len(better)} of {len(judged)} metrics ({named(better)}); "
                    f"worse on {len(worse)} ({named(worse)}).")  # fmt: skip
        state = "mixed"
    else:
        sentence = f"No meaningful change on {len(judged)} metrics."
        state = "same"
    changes = [c.describe() for c in cmp.config_diff.meaningful][:3]
    n_changes = len(cmp.config_diff.meaningful)
    changed = (
        ("Changed: " + "; ".join(changes) + (f" (+{n_changes - 3} more)" if n_changes > 3 else "") + ".")
        if changes
        else "No config change found."
    )
    return {
        "state": state,
        "sentence": sentence,
        "detail": changed,
        "better": len(better),
        "worse": len(worse),
        "judged": len(judged),
        "stage": stage,
    }
