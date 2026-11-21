"""Deterministic study checks (the pitfall catalog P1–P8) and the loss ledger.

Every check reads artefacts that already exist — configs, the logged metric names, the evaluator's JSONs, the LR
schedule — and returns PASS / FAIL / WARN / SKIP / INFO with the evidence it used. No AI in the loop.

* P1 ruler        runs compared on different ``combined_metrics_yaml`` rulers
* P2 glossary     metric names whose meaning differs from their nickname (``AP_per_distance`` = TP tolerance)
* P3 gt_coverage  a scored attribute whose ground truth sits ≥ 95 % in one bin (e.g. zeroed GT velocity)
* P4 monitor      ``best_checkpoint_monitor`` names a metric that is never logged
* P5 val_series   a training stage without any ``val/`` metric (checkpoint selection is blind)
* P6 provenance   every figure is regenerated from a named data source (by construction here)
* P7 paths        a muted auxiliary head is not the same as a muted modality
* P8 plateau      loss slope × LR at the plateau: converged / schedule-bound / learning / thermostat
"""

from __future__ import annotations

import fnmatch
import itertools
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import numpy as np

from model_forensic.analysis import evaljson
from model_forensic.domain.metrics import MetricKey, MetricSeries, Split
from model_forensic.infrastructure.connectors.naming import loss_family

STATUSES = ("FAIL", "WARN", "PASS", "INFO", "SKIP")


@dataclass
class CheckResult:
    id: str
    title: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------ helpers
def _tail(s: MetricSeries, frac: float = 0.1) -> float:
    _, v = s.finite()
    if not v.size:
        return math.nan
    return float(np.mean(v[-max(1, int(v.size * frac)) :]))


def _head(s: MetricSeries, frac: float = 0.1) -> float:
    _, v = s.finite()
    if not v.size:
        return math.nan
    return float(np.mean(v[: max(1, int(v.size * frac))]))


def config_values(flat: Mapping[str, Any], suffix: str) -> dict[str, Any]:
    return {k: v for k, v in flat.items() if k == suffix or k.endswith("." + suffix)}


def ruler_identity(path: str) -> str:
    """The comparable tail of a ruler path (install prefixes differ between images)."""
    parts = PurePosixPath(str(path)).parts
    return "/".join(parts[-3:]) if len(parts) >= 3 else str(path)


# ------------------------------------------------------------------------------------------------ P1
def check_ruler(configs: Mapping[str, Mapping[str, Any]]) -> CheckResult:
    rulers: dict[str, str | None] = {}
    for label, flat in configs.items():
        vals = [v for v in config_values(flat, "combined_metrics_yaml").values() if isinstance(v, str) and v]
        rulers[label] = ruler_identity(vals[0]) if vals else None
    known = {k: v for k, v in rulers.items() if v}
    if not known:
        return CheckResult(
            "P1",
            "Same ruler for every compared run",
            "SKIP",
            "no combined_metrics_yaml in the configs",
            {"rulers": rulers},
        )
    if len(configs) < 2:
        return CheckResult(
            "P1",
            "Same ruler for every compared run",
            "INFO",
            f"single run scored with {next(iter(known.values()))}",
            {"rulers": rulers},
        )
    distinct = sorted(set(known.values()))
    if len(distinct) > 1 or len(known) < len(rulers):
        detail = "; ".join(f"{k}: {v or 'unknown'}" for k, v in rulers.items())
        return CheckResult(
            "P1",
            "Same ruler for every compared run",
            "FAIL",
            f"RULER MISMATCH — headline combined scores are not comparable ({detail}). Compare the "
            "ruler-free per-class torch_metric_evaluation metrics instead.",
            {"rulers": rulers},
        )
    return CheckResult(
        "P1", "Same ruler for every compared run", "PASS", f"all runs use {distinct[0]}", {"rulers": rulers}
    )


# ------------------------------------------------------------------------------------------------ P2
def check_glossary(metric_names: Sequence[str], evals: Sequence[evaljson.EvalEvidence]) -> CheckResult:
    used = set()
    names = set(metric_names) | {m for e in evals for c in e.classes.values() for m in c.metrics}
    for pat, meaning in evaljson.GLOSSARY.items():
        if any(fnmatch.fnmatchcase(n.rsplit("/", 1)[-1], p) for n in names for p in pat.split("/")):
            used.add((pat, meaning))
    if not used:
        return CheckResult("P2", "Metric meaning, not nickname", "SKIP", "no metric with a known naming trap")
    return CheckResult(
        "P2",
        "Metric meaning, not nickname",
        "INFO",
        " · ".join(f"{p}: {m}" for p, m in sorted(used)),
        {"glossary": dict(sorted(used))},
    )


# ------------------------------------------------------------------------------------------------ P3
def check_gt_coverage(evals: Sequence[evaljson.EvalEvidence], share: float = 0.95) -> CheckResult:
    bad: dict[str, dict[str, float]] = {}
    seen = False
    for e in evals:
        for cls, ce in e.classes.items():
            if ce.gt_bins:
                seen = True
            deg = evaljson.gt_degenerate(ce, share)
            if deg:
                bad[f"{e.stage + '/' if e.stage else ''}{cls}"] = deg
    if not seen:
        return CheckResult(
            "P3",
            "Ground truth varies for every scored attribute",
            "SKIP",
            "no bb3d_match_evaluation bins downloaded (eval_json preset)",
        )
    if bad:
        detail = "; ".join(
            f"{k}: " + ", ".join(f"{a} {v:.0%} in one bin" for a, v in d.items()) for k, d in bad.items()
        )
        return CheckResult(
            "P3",
            "Ground truth varies for every scored attribute",
            "FAIL",
            f"degenerate ground truth — metrics on these attributes measure the prediction, not the error: {detail}",
            {"degenerate": bad},
        )
    return CheckResult(
        "P3",
        "Ground truth varies for every scored attribute",
        "PASS",
        f"no attribute with ≥{share:.0%} of its ground truth in one bin",
    )


# ------------------------------------------------------------------------------------------------ P4
def check_monitor(flat: Mapping[str, Any], logged: Sequence[str]) -> CheckResult:
    mons = {
        k: v
        for k, v in flat.items()
        if isinstance(v, str) and (k.endswith("checkpoint_monitor") or k.endswith(".monitor"))
    }
    if not mons:
        return CheckResult("P4", "Checkpoint monitor is a logged metric", "SKIP", "no checkpoint monitor in the config")
    logged_set = set(logged) | {x.split("[", 1)[0] for x in logged}
    missing = {
        k: v
        for k, v in mons.items()
        if v not in logged_set and not any(x.endswith("/" + v) or x.endswith(v) for x in logged_set)
    }
    if missing:
        return CheckResult(
            "P4",
            "Checkpoint monitor is a logged metric",
            "FAIL",
            ", ".join(repr(v) for v in sorted(set(missing.values())))
            + f" ({', '.join(sorted({k.rsplit('.', 1)[-1] for k in missing}))} in {len(missing)} config(s)) is never logged"
            + " — the 'best' checkpoint silently falls back (usually to last).",
            {"missing": missing},
        )
    return CheckResult(
        "P4", "Checkpoint monitor is a logged metric", "PASS", "; ".join(f"{k} = {v}" for k, v in mons.items())
    )


# ------------------------------------------------------------------------------------------------ P5
def check_val_series(series: Mapping[MetricKey, MetricSeries]) -> CheckResult:
    by_stage: dict[str, set[str]] = defaultdict(set)
    for k, s in series.items():
        if "stage" in k.dim_dict:
            continue
        by_stage[s.provenance.get("stage") or "(run)"].add(k.split.value)
    training = [st for st, splits in by_stage.items() if "train" in splits]
    if not training:
        return CheckResult("P5", "Training stages log validation metrics", "SKIP", "no training stage")
    blind = [st for st in training if "val" not in by_stage[st]]
    if blind:
        return CheckResult(
            "P5",
            "Training stages log validation metrics",
            "FAIL",
            f"no val/ metric in {', '.join(blind)} — checkpoint selection and early stopping are blind",
            {"stages": blind},
        )
    return CheckResult("P5", "Training stages log validation metrics", "PASS", f"val/ metrics in {', '.join(training)}")


# ------------------------------------------------------------------------------------------------ P8
def plateau(
    loss: MetricSeries | None, lr: MetricSeries | None, noreg: MetricSeries | None = None, segments: int = 10
) -> CheckResult:
    """Plateau triage — loss slope × LR (and total vs network-facing loss for the thermostat case)."""
    if loss is None or len(loss) < segments:
        return CheckResult("P8", "Plateau read with the learning rate", "SKIP", "no training loss curve")
    s, v = loss.finite()
    edges = np.linspace(s.min(), s.max(), segments + 1)
    means = [
        float(np.mean(v[(s >= a) & (s <= b)])) if np.any((s >= a) & (s <= b)) else math.nan
        for a, b in itertools.pairwise(edges)
    ]
    means = [m for m in means if math.isfinite(m)]
    if len(means) < 4:
        return CheckResult("P8", "Plateau read with the learning rate", "SKIP", "too few loss points")
    drop = means[0] - means[-1]
    if drop <= 0:
        return CheckResult(
            "P8",
            "Plateau read with the learning rate",
            "WARN",
            "the loss did not decrease overall",
            {"segment_means": means},
        )
    # Plateau: first segment after which the remaining improvement is < 5 % of the total drop.
    k = next((i for i in range(len(means)) if means[i] - means[-1] < 0.05 * drop), len(means) - 1)
    at = k / (len(means) - 1)
    last_slope = (means[-2] - means[-1]) / max(abs(means[0]), 1e-12)
    data: dict[str, Any] = {"segment_means": means, "plateau_fraction": at, "last_segment_gain": last_slope}
    lr_rel = None
    if lr is not None and len(lr):
        ls, lv = lr.finite()
        peak = float(lv.max()) if lv.size else math.nan
        step_at = float(edges[k])
        idx = int(np.argmin(np.abs(ls - step_at))) if ls.size else 0
        lr_rel = float(lv[idx] / peak) if peak else None
        data["lr_at_plateau_rel"] = lr_rel
    thermo = None
    if noreg is not None and len(noreg) >= segments:
        ns, nv = noreg.finite()
        half = s.min() + (s.max() - s.min()) / 2
        late_total = float(np.mean(v[s >= half][:3]) - np.mean(v[s >= half][-3:])) if np.sum(s >= half) >= 6 else 0.0
        late_net = float(np.mean(nv[ns >= half][:3]) - np.mean(nv[ns >= half][-3:])) if np.sum(ns >= half) >= 6 else 0.0
        if late_total > 0 and late_net < 0.25 * late_total:
            thermo = (late_total, late_net)
            data["thermostat"] = {"late_total_drop": late_total, "late_network_drop": late_net}
    if last_slope > 0.02:
        verdict, status = (
            "learning — the last segment still improves; let it run / compare final vs mid checkpoint",
            "PASS",
        )
    elif thermo is not None:
        verdict, status = (
            (
                "thermostat settling — the total still falls but the network-facing loss does not; only σ "
                "is adapting, the network is done"
            ),
            "WARN",
        )
    elif lr_rel is not None and lr_rel < 0.2:
        verdict, status = "schedule-bound — flat while the LR is nearly decayed; extend or re-warm the schedule", "WARN"
    elif lr_rel is not None and at < 0.5:
        verdict, status = (
            (
                f"converged early — flat from {at:.0%} of training while the LR is still at {lr_rel:.0%} of "
                f"peak; later epochs are wasted compute (change data, weights or capacity, not the schedule)"
            ),
            "WARN",
        )
    else:
        verdict, status = (
            "converged"
            + (
                f" (LR at {lr_rel:.0%} of peak at the plateau)"
                if lr_rel is not None
                else " (no LR schedule found to tell saturation from starvation)"
            ),
            "PASS",
        )
    return CheckResult(
        "P8", "Plateau read with the learning rate", status, f"plateau at {at:.0%} of training; {verdict}", data
    )


# ------------------------------------------------------------------------------------------------ loss ledger
@dataclass
class LedgerRow:
    term: str
    dims: tuple[tuple[str, str], ...]
    family: str
    raw: float
    weighted: float
    no_reg: float
    sigma: float
    weight: float  # task weight w = (weighted - no_reg) / sigma
    tax: float  # weighted - no_reg: the σ regularisation term (no gradient to the network)
    coef: float  # no_reg / raw: gradient reaching the network per unit raw loss

    @property
    def label(self) -> str:
        extra = ",".join(f"{a}={b}" for a, b in self.dims if a not in {"task"})
        return f"{self.term}[{extra}]" if extra else self.term


def ledger(series: Mapping[MetricKey, MetricSeries], frac: float = 0.1, at_start: bool = False) -> list[LedgerRow]:
    """Per-subloss ledger at the end (or start) of training from uloss / loss / loss_noreg / task_log_sigma."""
    pick = _head if at_start else _tail
    parts: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, float]] = defaultdict(dict)
    for k, s in series.items():
        if k.split != Split.TRAIN or "stage" in k.dim_dict:
            continue
        if k.name == "task_log_sigma":
            d = dict(k.dims)
            term = d.pop("term", "")
            parts[(term, tuple(sorted(d.items())))]["sigma"] = pick(s, frac)
        else:
            for pre, slot in (("uloss/", "raw"), ("loss_noreg/", "no_reg"), ("loss/", "weighted")):
                if k.name.startswith(pre):
                    parts[(k.name[len(pre) :], k.dims)][slot] = _tail(s, frac)
                    break
    rows = []
    for (term, dims), p in parts.items():
        if not {"raw", "weighted", "no_reg", "sigma"} <= set(p):
            continue
        sig = p["sigma"]
        tax = p["weighted"] - p["no_reg"]
        rows.append(
            LedgerRow(
                term,
                dims,
                loss_family(term, dims),
                p["raw"],
                p["weighted"],
                p["no_reg"],
                sig,
                tax / sig if abs(sig) > 0.02 else math.nan,
                tax,
                p["no_reg"] / p["raw"] if p["raw"] else math.nan,
            )
        )
    return sorted(rows, key=lambda r: (r.family, r.label))


def family_rollup(rows: Sequence[LedgerRow]) -> dict[str, dict[str, float]]:
    fam: dict[str, dict[str, float]] = {}
    for r in rows:
        f = fam.setdefault(
            r.family,
            {
                "raw": 0.0,
                "weighted": 0.0,
                "no_reg": 0.0,
                "tax": 0.0,
                "sigma_min": math.inf,
                "sigma_max": -math.inf,
                "n": 0,
            },
        )
        f["raw"] += r.raw
        f["weighted"] += r.weighted
        f["no_reg"] += r.no_reg
        f["tax"] += r.tax
        f["sigma_min"] = min(f["sigma_min"], r.sigma)
        f["sigma_max"] = max(f["sigma_max"], r.sigma)
        f["n"] += 1
    tot = {k: sum(abs(f[k]) for f in fam.values()) or 1.0 for k in ("raw", "weighted", "no_reg")}
    for f in fam.values():
        for k in ("raw", "weighted", "no_reg"):
            f[f"{k}_share"] = abs(f[k]) / tot[k]
    return fam
