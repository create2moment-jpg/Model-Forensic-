"""Public helpers on metric names and series, shared by the analyses (design review D8).

Before, analyses imported each other's private helpers (``_is_loss``, ``_parse``, ``_noise``, ``_reach``,
``_direction``, ``_fmt``, ``_tail_mean``, ``_early``) — hidden coupling. This module is the one place for them; the old private
names remain as aliases inside their modules only.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from model_forensic.analysis import signal
from model_forensic.domain.metrics import MetricSeries

if TYPE_CHECKING:
    from model_forensic.analysis.metric_registry import MetricRegistry


# ── names ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_key(k: str) -> tuple[str, dict[str, str]]:
    """``train/loss/cls[layer=3]`` → ``("train/loss/cls", {"layer": "3"})``."""
    i = k.find("[")
    if i < 0 or not k.endswith("]"):
        return k, {}
    return k[:i], dict(p.split("=", 1) for p in k[i + 1 : -1].split(",") if "=" in p)


def is_loss(k: str) -> bool:
    """A loss series (not a learned weight / σ that happens to contain 'loss')."""
    fam = parse_key(k)[0].lower()
    return "loss" in fam and not re.search(r"sigma|log_?var|uncert|weight", fam)


def is_val(k: str) -> bool:
    return bool(re.match(r"^(val|valid|validation|test|eval)[/_.]", k, re.I))


def direction(name: str, registry: MetricRegistry | None) -> bool | None:
    """Higher is better (True), lower is better (False), or unknown (None)."""
    hib = registry.lookup(name).higher_is_better if registry is not None else None
    if hib is None and ("loss" in name or name.endswith(("_err", "error", "rmse", "rsme"))):
        return False
    return hib


def fmt(v: float) -> str:
    a = abs(v)
    return f"{v:.3e}" if a and (a < 1e-3 or a >= 1e5) else f"{v:.4g}"


# ── series ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def noise(v: npt.NDArray[np.float64]) -> float:
    """Step-to-step noise of the second half relative to its level (MAD of the residual around a rolling median)."""
    half = v[v.size // 2 :]
    if half.size < 10:
        return math.nan
    resid = half - signal.rolling_median(half, max(5, half.size // 20))
    level = float(np.median(np.abs(half))) or 1.0
    return signal.mad(resid) / level


def reach(s: MetricSeries) -> dict[str, float | None]:
    """Share of the schedule at which the smoothed loss has made 50% / 90% of its drop (None if it does not fall)."""
    st, v = s.finite()
    if v.size < 8:
        return {"t50": None, "t90": None}
    sm = signal.ema(v, 0.2)
    drop = float(sm[0] - sm.min())
    if drop <= 0 or drop < 0.02 * abs(float(sm[0]) or 1.0):
        return {"t50": None, "t90": None}
    span = float(st[-1] - st[0]) or 1.0
    out: dict[str, float | None] = {}
    for name, p in (("t50", 0.5), ("t90", 0.9)):
        i = next(i for i in range(sm.size) if sm[0] - sm[i] >= p * drop)
        out[name] = float((st[i] - st[0]) / span)
    return out


def tail_mean(s: MetricSeries, frac: float = 0.2) -> float:
    _, v = s.finite()
    return float(np.mean(signal.tail(v, frac, min_points=1))) if v.size else math.nan


def early(s: MetricSeries, frac: float = 0.2) -> float:
    """Median of the first ``frac`` of the values."""
    _, v = s.finite()
    if not v.size:
        return math.nan
    k = max(1, round(v.size * frac))
    return float(np.median(v[:k]))
