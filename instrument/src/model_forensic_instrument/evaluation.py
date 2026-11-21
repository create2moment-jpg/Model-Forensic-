"""Evaluation breakdowns: per-slice metrics (range, class, weather, ...) and nuScenes summaries."""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from model_forensic_instrument.naming import metric_name
from model_forensic_instrument.sinks import Sink

DEFAULT_RANGE_BINS = {"0-30": (0.0, 30.0), "30-60": (30.0, 60.0), "60-inf": (60.0, float("inf"))}


def log_slices(
    sink: Sink, step: int, metric: str, values: Mapping[str, float], dim: str = "range", split: str = "val"
) -> Dict[str, float]:
    """``log_slices(sink, step, "recall", {"0-30": .78, "60-inf": .41})`` → ``val/recall[range=60-inf]`` etc."""
    out = {metric_name(metric, split, **{dim: k}): float(v) for k, v in values.items()}
    sink.log(out, step)
    return out


def evaluate_by_slices(
    evaluate: Callable[[Callable[[Any], bool]], Mapping[str, float]],
    slices: Mapping[str, Callable[[Any], bool]],
    dim: str = "range",
    sink: Optional[Sink] = None,
    step: int = 0,
    split: str = "val",
) -> Dict[str, Dict[str, float]]:
    """Re-run an evaluator on subsets.

    ``evaluate(keep)`` must evaluate only ground-truth boxes (and their matched predictions) for which
    ``keep(box)`` is true — most evaluators accept a GT/pred filter. For range slices::

        bins = {name: (lambda b, lo=lo, hi=hi: lo <= (b.x**2 + b.y**2) ** .5 < hi)
                for name, (lo, hi) in DEFAULT_RANGE_BINS.items()}
        evaluate_by_slices(my_eval, bins, dim="range", sink=fx.sink, step=step)
    """
    results: Dict[str, Dict[str, float]] = {}
    for name, keep in slices.items():
        metrics = dict(evaluate(keep))
        results[name] = metrics
        if sink is not None:
            sink.log({metric_name(k, split, **{dim: name}): float(v) for k, v in metrics.items()}, step)
    return results


_TP_ERRORS = {"trans_err": "ATE", "scale_err": "ASE", "orient_err": "AOE", "vel_err": "AVE", "attr_err": "AAE"}


def nuscenes_breakdown(summary: Mapping[str, Any], split: str = "val") -> Dict[str, float]:
    """Flatten a nuScenes detection ``metrics_summary.json`` into canonical metrics.

    Headline: ``mAP``, ``NDS``, ``mATE`` … ; per class: ``AP[class=car]`` (mean over distance thresholds),
    ``AP[class=car,thr=2.0]``, ``ATE[class=car]`` … — the per-class / per-threshold structure is what lets
    Model Forensic localize regressions.
    """
    out: Dict[str, float] = {}
    if "mean_ap" in summary:
        out[metric_name("mAP", split)] = float(summary["mean_ap"])
    if "nd_score" in summary:
        out[metric_name("NDS", split)] = float(summary["nd_score"])
    for err, short in _TP_ERRORS.items():
        if err in summary.get("tp_errors", {}):
            out[metric_name(f"m{short}", split)] = float(summary["tp_errors"][err])
    for cls, v in summary.get("mean_dist_aps", {}).items():
        out[metric_name("AP", split, **{"class": cls})] = float(v)
    for cls, by_thr in summary.get("label_aps", {}).items():
        for thr, v in by_thr.items():
            out[metric_name("AP", split, **{"class": cls, "thr": thr})] = float(v)
    for cls, errs in summary.get("label_tp_errors", {}).items():
        for err, v in errs.items():
            if err in _TP_ERRORS and v == v:  # skip NaN (not applicable for some classes)
                out[metric_name(_TP_ERRORS[err], split, **{"class": cls})] = float(v)
    return out
