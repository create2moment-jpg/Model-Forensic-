"""Uncertainty-based loss weighting — a dedicated forensic section.

Multi-task losses weighted by learned uncertainty (Kendall et al. 2018): ``L = Σ_i exp(-2·s_i)·L_i + s_i`` with
``s_i = log σ_i`` learned per head. What goes wrong in practice: a head whose σ grows is quietly switched off; a
σ driven to its lower bound blows the weight up; σ still drifting at the end means the weights never settled; and
the total loss can fall only because the weights moved, not because any task improved.

``report(svc, run)`` gives, per head: log σ start → end, effective weight exp(-2·log σ) start → end and its share
at the end, the drift over the last 10 %, whether it sits on a configured bound, the head's own (unweighted) loss
progress, and a status (switched_off · dominant · at_bound · drifting · settled). It adds the config keys that set
the weighting, the findings of the ``task_weighting`` analysis, and validated plot specs. With no learned σ in
the run it says so (missing data) — it never invents heads.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from model_forensic.application.plotting import validate
from model_forensic.domain.messages import ParseMessage
from model_forensic.domain.metrics import MetricSeries, Split
from model_forensic.domain.plotspec import PlotSpecV1, SeriesRef

OFF_SHARE = 0.02
DOMINANT_SHARE = 0.5
DRIFT = 0.05


def _head(s: MetricSeries) -> tuple[str, str]:
    d = dict(s.key.dims)
    term = d.pop("term", "")
    rest = ",".join(f"{k}={v}" for k, v in sorted(d.items()) if k != "task")
    return term, f"{term}[{rest}]" if rest else term


def _bounds(cfg: dict[str, Any]) -> tuple[dict[str, Any], list[float] | None]:
    keys = {k: v for k, v in cfg.items() if any(t in k.lower() for t in ("log_sigma", "uncertainty", "task_weight"))}
    keys = {
        k: v for k, v in keys.items() if not any(j != k and k.endswith("." + j) for j in keys)
    }  # file-prefixed copies
    b = None
    for k, v in keys.items():
        if "bound" in k.lower() and isinstance(v, (list, tuple)) and len(v) == 2:
            try:
                b = [float(v[0]), float(v[1])]
            except (TypeError, ValueError):
                b = None
    return keys, b


def report(svc: Any, run: str) -> dict[str, Any]:
    ref = svc.ref(run)
    rid = str(ref)
    series = svc.series(ref)
    sig = [
        s
        for s in series
        if s.key.name == "task_log_sigma" and s.key.split in (Split.TRAIN, Split.UNKNOWN) and len(s) >= 3
    ]
    if not sig:
        return {
            "available": False,
            "message": ParseMessage(
                "missing_data", "uncertainty.none",
                "no learned task weights in this run (train/task_log_sigma[term=…], e.g. …/uncertainty_weight_log_sigma)",
                remediation="if the model uses uncertainty weighting, log log σ per head; a project profile can map "
                "another metric name to task_log_sigma",
            ).to_dict(),
        }  # fmt: skip
    try:
        cfg = svc.flat_config(ref)
    except Exception:
        cfg = {}
    cfg_keys, bounds = _bounds(cfg)
    uloss = {
        (s.key.name.removeprefix("uloss/"), dict(s.key.dims).get("layer")): s
        for s in series
        if s.key.name.startswith("uloss/")
    }
    rows: list[dict[str, Any]] = []
    for s in sig:
        _, v = s.finite()
        k = max(1, v.size // 10)
        first, last = float(np.median(v[:k])), float(np.mean(v[-k:]))
        tail = v[-max(2, k) :]
        drift = float(tail[-1] - tail[0])
        term, label = _head(s)
        ul = uloss.get((term, dict(s.key.dims).get("layer")))
        prog = None
        if ul is not None:
            _, u = ul.finite()
            if u.size >= 3:
                a, b = float(np.median(u[: max(1, u.size // 10)])), float(np.mean(u[-max(1, u.size // 10) :]))
                prog = round((a - b) / abs(a), 4) if a else None
        rows.append({"head": label, "term": term, "key": str(s.key), "log_sigma": {"first": round(first, 4), "last": round(last, 4)},
                     "weight": {"first": round(math.exp(-2 * first), 4), "last": round(math.exp(-2 * last), 4)},
                     "drift_last_10pct": round(drift, 4), "unweighted_loss_progress": prog,
                     "unweighted_key": str(ul.key) if ul is not None else None})  # fmt: skip
    total = sum(r["weight"]["last"] for r in rows) or 1.0
    for r in rows:
        r["share"] = round(r["weight"]["last"] / total, 4)
        at_bound = bounds is not None and any(abs(r["log_sigma"]["last"] - b) < 1e-2 for b in bounds)
        r["status"] = (
            "switched_off" if r["share"] < OFF_SHARE
            else "at_bound" if at_bound
            else "dominant" if len(rows) >= 3 and r["share"] > DOMINANT_SHARE
            else "drifting" if abs(r["drift_last_10pct"]) >= DRIFT
            else "settled"
        )  # fmt: skip
        r["why"] = {
            "switched_off": f"its weight is {r['share']:.1%} of the total — the model has effectively stopped training it"
            + (" while its own loss is not improving" if (r["unweighted_loss_progress"] or 0) < 0.05 else ""),
            "at_bound": f"log σ sits on the configured bound {bounds} — the weight is limited by the clamp, not learned",
            "dominant": f"it takes {r['share']:.0%} of the weight — the other heads are learned in its shadow",
            "drifting": f"log σ still moved {r['drift_last_10pct']:+.2f} in the last 10 % — the weights have not settled",
            "settled": "stable weight at the end",
        }[r["status"]]
    rows.sort(key=lambda r: r["share"])
    findings: list[dict[str, Any]] = []
    try:
        from model_forensic.analysis.base import AnalysisContext
        from model_forensic.analysis.plugins.heads import TaskWeighting, TaskWeightingParams

        res = TaskWeighting().run(AnalysisContext(target=svc.run_data(ref)), TaskWeightingParams())
        findings = [f.model_dump(mode="json") for f in res.findings]
    except Exception as exc:  # the section still shows the measured table
        findings = [{"title": "task_weighting analysis failed", "summary": f"{type(exc).__name__}: {exc}"}]
    plots = [
        PlotSpecV1(id="uw-log-sigma", title="Learned log σ per head", question="How did each head's uncertainty move?",
                   source="analysis", series=[SeriesRef(run=rid, key=r["key"], label=r["head"]) for r in rows[:12]]),
        PlotSpecV1(id="uw-weight", title="Effective weight exp(-2·log σ)", question="Which heads were up- or down-weighted?",
                   source="analysis", series=[SeriesRef(run=rid, key=r["key"], label=r["head"], transform="exp_neg2") for r in rows[:12]]),
    ]  # fmt: skip
    ul_refs = [
        SeriesRef(run=rid, key=r["unweighted_key"], label=r["head"], transform="normalize")
        for r in rows
        if r["unweighted_key"]
    ][:12]
    if ul_refs:
        plots.append(PlotSpecV1(id="uw-unweighted", title="Unweighted loss per head (relative to start)",
                                question="Did the tasks improve, or only the weights move?", source="analysis", series=ul_refs))  # fmt: skip
    valid = [p.model_dump() for p in plots if validate(p.model_dump(), svc)[0] is not None]
    return {
        "available": True,
        "run": rid,
        "heads": rows,
        "summary": {
            "heads": len(rows),
            "switched_off": sum(r["status"] == "switched_off" for r in rows),
            "at_bound": sum(r["status"] == "at_bound" for r in rows),
            "drifting": sum(r["status"] == "drifting" for r in rows),
            "dominant": sum(r["status"] == "dominant" for r in rows),
        },
        "config": {
            k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v))
            for k, v in sorted(cfg_keys.items())
        },
        "bounds": bounds,
        "findings": findings,
        "plots": valid,
        "formula": "L = Σ_i exp(-2·log σ_i)·L_i + log σ_i  (Kendall et al. 2018)",
    }
