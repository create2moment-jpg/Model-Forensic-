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


def _aligned(series: list[MetricSeries]) -> tuple[np.ndarray, list[np.ndarray]]:
    """Common steps of several series (the union), each series carried forward to every step (NaN before it starts)."""
    steps = np.unique(np.concatenate([np.asarray(x.steps, dtype=float) for x in series]))
    out = []
    for x in series:
        st = np.asarray(x.steps, dtype=float)
        v = np.asarray(x.values, dtype=float)
        idx = np.searchsorted(st, steps, side="right") - 1
        a = np.where(idx >= 0, v[np.clip(idx, 0, len(v) - 1)], np.nan)
        out.append(a)
    return steps, out


def _thin(n: int, max_points: int = 400) -> np.ndarray:
    return np.unique(np.linspace(0, n - 1, min(n, max_points)).astype(int)) if n else np.array([], dtype=int)


def _first_step(steps: np.ndarray, cond: np.ndarray, stays: bool = True) -> float | None:
    """First step from which ``cond`` holds (until the end when ``stays``)."""
    if not cond.size or (not cond[-1] and stays):
        return None
    if stays:
        bad = np.where(~cond)[0]
        i = int(bad[-1]) + 1 if bad.size else 0
        return float(steps[i]) if i < steps.size else None
    hit = np.where(cond)[0]
    return float(steps[int(hit[0])]) if hit.size else None


def _over_time(
    rows: list[dict[str, Any]], sig: list[MetricSeries], losses: list[MetricSeries | None]
) -> dict[str, Any]:
    """Share of the total weight per head over time, the weighted-loss share (where the head's own loss is logged),
    the balance (normalised entropy, effective number of tasks), and per-head events."""
    steps, logs = _aligned(sig)
    w = np.exp(-2.0 * np.vstack(logs))  # heads × steps
    ok = np.all(np.isfinite(w), axis=0)
    steps, w = steps[ok], w[:, ok]
    if steps.size < 3:
        return {}
    share = w / w.sum(axis=0, keepdims=True)
    n = share.shape[0]
    with np.errstate(divide="ignore", invalid="ignore"):
        ent = -np.nansum(np.where(share > 0, share * np.log(share), 0.0), axis=0)
    effective = np.exp(ent)
    balance = ent / math.log(n) if n > 1 else np.ones_like(ent)
    contrib = None
    if any(x is not None for x in losses):
        cols = []
        for i, x in enumerate(losses):
            if x is None:
                cols.append(np.full(steps.size, np.nan))
                continue
            st = np.asarray(x.steps, dtype=float)
            idx = np.searchsorted(st, steps, side="right") - 1
            v = np.asarray(x.values, dtype=float)
            cols.append(np.where(idx >= 0, v[np.clip(idx, 0, len(v) - 1)], np.nan) * w[i])
        c = np.vstack(cols)
        tot = np.nansum(c, axis=0)
        contrib = np.where(tot > 0, c / np.where(tot > 0, tot, 1), np.nan)
    for i, row in enumerate(rows):
        sh = share[i]
        row["share_min"], row["share_max"] = round(float(sh.min()), 4), round(float(sh.max()), 4)
        row["switched_off_at"] = _first_step(steps, sh < OFF_SHARE)
        logs_i = -0.5 * np.log(w[i])
        settled = np.abs(logs_i - logs_i[-1]) < DRIFT
        row["settled_at"] = _first_step(steps, settled)
        x = losses[i]
        row["optimum_gap"] = row["sigma_loss_corr"] = None
        if x is not None:
            st = np.asarray(x.steps, dtype=float)
            v = np.asarray(x.values, dtype=float)
            idx = np.searchsorted(st, steps, side="right") - 1
            lv = np.where(idx >= 0, v[np.clip(idx, 0, len(v) - 1)], np.nan)
            good = np.isfinite(lv) & (lv > 0)
            if good.sum() >= 5:
                # for L·exp(-2s) + s the optimum is s* = ½·log(2L): positive gap → σ larger than optimal (under-weighted)
                opt = 0.5 * np.log(2 * lv[good])
                row["optimum_gap"] = round(float(logs_i[good][-1] - opt[-1]), 4)
                if np.std(logs_i[good]) > 0 and np.std(np.log(lv[good])) > 0:
                    row["sigma_loss_corr"] = round(float(np.corrcoef(logs_i[good], np.log(lv[good]))[0, 1]), 3)
    pick = _thin(steps.size)
    heads = [r["head"] for r in rows]
    return {
        "steps": steps[pick].tolist(),
        "share": {h: [round(float(x), 5) for x in share[i][pick]] for i, h in enumerate(heads)},
        "contribution": (
            {h: [None if not np.isfinite(x) else round(float(x), 5) for x in contrib[i][pick]] for i, h in enumerate(heads)}
            if contrib is not None else None
        ),
        "effective_tasks": [round(float(x), 4) for x in effective[pick]],
        "balance": [round(float(x), 4) for x in balance[pick]],
        "balance_summary": {
            "effective_tasks_start": round(float(effective[0]), 3), "effective_tasks_end": round(float(effective[-1]), 3),
            "balance_start": round(float(balance[0]), 3), "balance_end": round(float(balance[-1]), 3),
            "max_min_ratio_end": round(float(np.max(share[:, -1])) / max(float(np.min(share[:, -1])), 1e-12), 2), "heads": n,
        },
    }  # fmt: skip


def report(svc: Any, run: str, baseline: str | None = None, stage: str | None = None) -> dict[str, Any]:
    """``stage``: read what that pipeline stage logged (the baseline too, when it has that stage)."""
    ref = svc.ref(run)
    rid = str(ref)
    series = svc.series(ref)
    st = stage if stage and stage in [n for n, _ in svc.stages(ref)] else ""
    if st:
        from model_forensic.application.stages import stage_view

        series = stage_view(series, st)
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
    loss_of: list[MetricSeries | None] = []
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
        loss_of.append(ul)
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
    timeline = _over_time(rows, sig, loss_of)
    base_info: dict[str, Any] | None = None
    if baseline:
        try:
            bres: dict[str, Any] = report(svc, baseline, stage=stage)
        except Exception as exc:
            base_info = {"run": str(svc.ref(baseline)), "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        else:
            if not bres.get("available"):
                base_info = {"run": str(svc.ref(baseline)), "error": "the baseline has no learned task weights"}
            else:
                bshare = {h["head"]: h for h in bres["heads"]}
                for row in rows:
                    bh = bshare.get(row["head"])
                    row["baseline_share"] = bh["share"] if bh else None
                    row["share_delta"] = round(row["share"] - bh["share"], 4) if bh else None
                    row["baseline_status"] = bh["status"] if bh else None
                base_info = {"run": bres["run"], "summary": bres["summary"],
                             "balance": (bres.get("timeline") or {}).get("balance_summary"),
                             "only_here": sorted(set(r["head"] for r in rows) - set(bshare)),
                             "only_there": sorted(set(bshare) - set(r["head"] for r in rows))}  # fmt: skip
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
                   source="analysis", series=[SeriesRef(run=rid, stage=st, key=r["key"], label=r["head"]) for r in rows[:12]]),
        PlotSpecV1(id="uw-weight", title="Effective weight exp(-2·log σ)", question="Which heads were up- or down-weighted?",
                   source="analysis", series=[SeriesRef(run=rid, stage=st, key=r["key"], label=r["head"], transform="exp_neg2") for r in rows[:12]]),
    ]  # fmt: skip
    ul_refs = [
        SeriesRef(run=rid, stage=st, key=r["unweighted_key"], label=r["head"], transform="normalize")
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
        "timeline": timeline,
        "baseline": base_info,
        "formula": "L = Σ_i exp(-2·log σ_i)·L_i + log σ_i  (Kendall et al. 2018)",
    }
