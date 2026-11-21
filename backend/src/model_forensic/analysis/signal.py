"""Robust 1-D signal primitives used by training-dynamics and loss analyses.

All functions are pure, numpy-only, and tolerate short or noisy series.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def ema(values: np.ndarray, alpha: float = 0.1) -> np.ndarray:
    """Debiased exponential moving average (TensorBoard-style smoothing)."""
    out = np.empty_like(values, dtype=float)
    acc, weight = 0.0, 0.0
    for i, v in enumerate(values):
        acc = (1 - alpha) * acc + alpha * v
        weight = (1 - alpha) * weight + alpha
        out[i] = acc / weight
    return out


def rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    n = values.size
    if n == 0:
        return np.array(values, dtype=float)
    w = max(1, min(window, n))
    half = w // 2
    padded = np.pad(values, (half, w - 1 - half), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, w)
    return np.asarray(np.median(view, axis=1), dtype=float)


def mad(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(np.median(np.abs(values - np.median(values))) * 1.4826)


@dataclass(frozen=True)
class Spike:
    index: int
    step: float
    value: float
    robust_z: float


def detect_spikes(
    steps: np.ndarray,
    values: np.ndarray,
    z: float = 6.0,
    window: int = 21,
    min_rel_jump: float = 0.15,
    skip_frac: float = 0.05,
) -> list[Spike]:
    """Points far above a rolling median: robust z (MAD of residual) above ``z`` AND at least
    ``min_rel_jump`` above the local median. The first ``skip_frac`` of points (steep initial
    descent / warmup) are ignored to avoid flagging the normal early transient."""
    if values.size < 5:
        return []
    base = rolling_median(values, window)
    resid = values - base
    scale = mad(resid) or (float(np.std(resid)) or 1e-12)
    zs = resid / scale
    rel = resid / np.maximum(np.abs(base), 1e-12)
    start = int(values.size * skip_frac)
    idx = [i for i in np.flatnonzero((zs > z) & (rel > min_rel_jump)) if i >= start]
    return [Spike(int(i), float(steps[i]), float(values[i]), float(zs[i])) for i in idx]


def tail(values: np.ndarray, frac: float = 0.2, min_points: int = 3) -> np.ndarray:
    k = max(min_points, round(values.size * frac))
    return values[-k:]


def relative_improvement(values: np.ndarray, frac: float = 0.2, lower_is_better: bool = True) -> float:
    """Relative improvement of the smoothed series over the final ``frac`` of training."""
    if values.size < 6:
        return float("nan")
    sm = ema(values, alpha=0.2)
    t = tail(sm, frac)
    start, end = t[0], t[-1]
    denom = abs(start) or 1e-12
    delta = (start - end) if lower_is_better else (end - start)
    return float(delta / denom)


def normalized_slope(steps: np.ndarray, values: np.ndarray) -> float:
    """Least-squares slope expressed as relative change of the mean per full step range."""
    if values.size < 3 or np.ptp(steps) == 0:
        return 0.0
    x = (steps - steps.min()) / np.ptp(steps)
    slope = float(np.polyfit(x, values, 1)[0])
    return slope / (abs(float(np.mean(values))) or 1e-12)


def oscillation_index(values: np.ndarray) -> float:
    """Fraction of sign changes in the first difference of the lightly smoothed series,
    weighted by amplitude relative to the overall trend. ~0 for monotone curves, ->1 for zig-zag."""
    if values.size < 8:
        return 0.0
    sm = ema(values, alpha=0.5)
    d = np.diff(sm)
    if not np.any(d):
        return 0.0
    signs = np.sign(d[d != 0])
    flips = float(np.mean(signs[1:] != signs[:-1])) if signs.size > 1 else 0.0
    trend = abs(sm[-1] - sm[0]) or 1e-12
    amplitude = float(np.mean(np.abs(d))) * d.size
    return flips * min(1.0, amplitude / (amplitude + trend))


def argmin_index(values: np.ndarray) -> int:
    return int(np.nanargmin(values))


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def align(sa: np.ndarray, va: np.ndarray, sb: np.ndarray, vb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate series b onto the overlapping steps of series a."""
    lo, hi = max(sa.min(), sb.min()), min(sa.max(), sb.max())
    mask = (sa >= lo) & (sa <= hi)
    s = sa[mask]
    return s, va[mask], np.interp(s, sb, vb)
