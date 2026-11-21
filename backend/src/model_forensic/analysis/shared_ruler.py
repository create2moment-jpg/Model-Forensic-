"""Ruler-free comparison and failure localisation from the per-class evaluation JSONs.

``torch_metric_evaluation`` is written for every run whatever its combined-metrics ruler, so it is the one
scoreboard that is comparable across rulers (check P1). Failure localisation reads one axis per artifact family:
distance (``pr_curves_detailed``), scenes (``false_positives_analysis``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from model_forensic.analysis.evaljson import EvalEvidence

# Direction of the ruler-free metrics (others are reported without a winner).
HIGHER = {
    "AP",
    "AP_per_distance_0_5",
    "AP_per_distance_1_0",
    "AP_per_distance_2_0",
    "AP_per_distance_4_0",
    "mota",
    "idf1",
    "assa",
    "assa_alpha",
    "hota",
    "deta",
    "motp_recall",
}
LOWER = {"ATE", "ASE", "AOE", "AVE", "AAE"}
DEFAULT_METRICS = ("AP", "AP_per_distance_0_5", "AP_per_distance_4_0", "ATE", "AOE", "AVE", "mota", "idf1")


@dataclass
class Cell:
    cls: str
    metric: str
    values: dict[str, float]
    best: str | None = None


@dataclass
class SharedTable:
    runs: list[str]
    cells: list[Cell] = field(default_factory=list)

    def classes(self) -> list[str]:
        return list(dict.fromkeys(c.cls for c in self.cells))

    def wins(self, reference: str) -> dict[str, tuple[int, int]]:
        """Per run: (cells better than ``reference``, cells compared) on directional metrics."""
        out: dict[str, tuple[int, int]] = {}
        for r in self.runs:
            if r == reference:
                continue
            better = total = 0
            for c in self.cells:
                if reference in c.values and r in c.values and (c.metric in HIGHER or c.metric in LOWER):
                    total += 1
                    a, b = c.values[r], c.values[reference]
                    better += int(a > b if c.metric in HIGHER else a < b)
            out[r] = (better, total)
        return out


def last_eval(evs: Sequence[EvalEvidence]) -> EvalEvidence | None:
    """The run's final evaluation (pipelines: the last evaluation stage)."""
    with_metrics = [e for e in evs if any(c.metrics for c in e.classes.values())]
    return with_metrics[-1] if with_metrics else (evs[-1] if evs else None)


def shared_table(
    evs: Mapping[str, EvalEvidence],
    classes: Sequence[str] | None = None,
    metrics: Sequence[str] = DEFAULT_METRICS,
    deprioritized: Sequence[str] = (),
) -> SharedTable:
    depri = {c.lower() for c in deprioritized}
    runs = list(evs)
    all_cls = list(dict.fromkeys(c for e in evs.values() for c in e.classes))
    cls_list = [c for c in (classes or all_cls) if c.lower() not in depri]
    t = SharedTable(runs=runs)
    for c in cls_list:
        for m in metrics:
            vals = {r: e.classes[c].metrics[m] for r, e in evs.items() if c in e.classes and m in e.classes[c].metrics}
            if not vals:
                continue
            best = None
            if len(vals) > 1 and (m in HIGHER or m in LOWER):
                best = (max if m in HIGHER else min)(vals, key=lambda r: vals[r])
            t.cells.append(Cell(c, m, vals, best))
    return t


@dataclass
class RangeDecay:
    cls: str
    recall: str
    distances: list[float]
    auc: list[float]
    near: float
    far: float
    knee: float | None  # first distance where AUC < 50 % of the nearest bucket

    @property
    def retained(self) -> float:
        return self.far / self.near if self.near else math.nan


def range_decay(ev: EvalEvidence, deprioritized: Sequence[str] = ()) -> list[RangeDecay]:
    depri = {c.lower() for c in deprioritized}
    out = []
    for cls, ce in ev.classes.items():
        if cls.lower() in depri or not ce.auc_by_distance:
            continue
        recall = min(ce.auc_by_distance, key=lambda r: float(r) if r.replace(".", "", 1).isdigit() else 9)
        pts = sorted(ce.auc_by_distance[recall].items())
        if len(pts) < 2:
            continue
        d, a = [p[0] for p in pts], [p[1] for p in pts]
        knee = next((di for di, ai in zip(d, a, strict=True) if a[0] and ai < 0.5 * a[0]), None)
        out.append(RangeDecay(cls, recall, d, a, a[0], a[-1], knee))
    return sorted(out, key=lambda r: r.retained)


@dataclass
class FpTail:
    cls: str
    fppf: float | None
    worst: list[tuple[str, float]]
    top_share: float  # share of the listed FP mass in the top 10 % of the listed sequences


def fp_tails(ev: EvalEvidence, deprioritized: Sequence[str] = ()) -> list[FpTail]:
    depri = {c.lower() for c in deprioritized}
    out = []
    for cls, ce in ev.classes.items():
        if cls.lower() in depri or not ce.worst_fp:
            continue
        vals = [v for _, v in ce.worst_fp]
        k = max(1, len(vals) // 10)
        share = sum(vals[:k]) / sum(vals) if sum(vals) else 0.0
        fppf = max(ce.fppf.values()) if ce.fppf else None
        out.append(FpTail(cls, fppf, ce.worst_fp[:10], share))
    return sorted(out, key=lambda f: -(f.fppf or 0))
