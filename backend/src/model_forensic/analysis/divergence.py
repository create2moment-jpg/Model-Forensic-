"""Where and why runs diverge: curves of 2+ runs against a reference run.

For each run and metric the curve is aligned with the reference on their common steps, both are smoothed, and the
direction-aware relative gap is compared with the step-to-step noise of the two runs. The **divergence point** is the
first step from which the gap stays outside the noise for a sustained stretch. The outcome says whether the gap holds
to the end (ahead / behind), closes again, flips sign (crossed), or never leaves the noise (same).

The *why* is a hypothesis, never a fact: learning-rate events near the divergence point, and the config changes whose
kind fits the timing (a change that diverges from the first steps fits architecture / data / loss changes; one that
diverges later, at an LR event, fits the schedule).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from model_forensic.analysis import signal
from model_forensic.analysis.chart_insights import _direction, _fmt
from model_forensic.analysis.metric_registry import MetricRegistry
from model_forensic.domain.config import ConfigChange
from model_forensic.domain.metrics import MetricKey, MetricSeries

EARLY_CATEGORIES = ("architecture", "modality", "data", "loss", "temporal")
LATE_CATEGORIES = ("optimization",)


@dataclass
class Divergence:
    run: str
    outcome: str  # same | ahead | behind | closed | crossed | insufficient
    diverge_step: float | None = None
    diverge_fraction: float | None = None
    final_gap: float | None = None  # direction-aware relative gap at the end (+ = better than the reference)
    max_gap: float | None = None
    threshold: float | None = None
    sentence: str = ""
    lr_note: str = ""
    fits: list[str] = field(default_factory=list)  # config changes that fit the timing (hypotheses)
    seed_n: int = 0  # reference seed replicates used for the noise floor (0 = none)
    seed_z: float | None = None  # (final − mean of the reference seeds) / their std, direction-aware
    seed_verdict: str = ""  # beyond seed noise | within seed noise

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _noise(v: npt.NDArray[np.float64]) -> float:
    half = v[v.size // 2 :]
    if half.size < 10:
        return math.nan
    resid = half - signal.rolling_median(half, max(5, half.size // 20))
    level = float(np.median(np.abs(half))) or 1.0
    return signal.mad(resid) / level


def _lr_events(lr: MetricSeries | None) -> list[float]:
    if lr is None or len(lr) < 2:
        return []
    s, v = lr.finite()
    return [float(s[i]) for i in range(1, v.size) if v[i - 1] > 0 and v[i] < 0.5 * v[i - 1]]


def _lr_at(lr: MetricSeries | None, step: float) -> float | None:
    if lr is None or len(lr) == 0:
        return None
    s, v = lr.finite()
    return float(np.interp(step, s, v)) if s.size else None


def _change_text(c: ConfigChange) -> str:
    if c.kind.value == "added":
        return f"{c.path} added ({_short(c.candidate)})"
    if c.kind.value == "removed":
        return f"{c.path} removed (was {_short(c.baseline)})"
    return f"{c.path}: {_short(c.baseline)} → {_short(c.candidate)}"


def _short(v: Any) -> str:
    t = str(v)
    return t if len(t) <= 40 else t[:37] + "…"


def diverge(
    run: str,
    ref: MetricSeries,
    cand: MetricSeries,
    *,
    registry: MetricRegistry | None = None,
    ref_lr: MetricSeries | None = None,
    cand_lr: MetricSeries | None = None,
    changes: tuple[ConfigChange, ...] | list[ConfigChange] = (),
    min_gap: float = 0.005,
    sparse_gap: float = 0.01,
) -> Divergence:
    hib = _direction(ref.key.name, registry)
    rs, rv = ref.finite()
    cs, cv = cand.finite()
    if rs.size < 2 or cs.size < 2:
        return Divergence(run, "insufficient", sentence=f"{run}: too few values to compare.")
    lo, hi = max(rs.min(), cs.min()), min(rs.max(), cs.max())
    mask = (rs >= lo) & (rs <= hi)
    steps = rs[mask]
    if steps.size < 2:
        return Divergence(run, "insufficient", sentence=f"{run}: the runs share no steps on this metric.")
    order = np.argsort(cs)
    c_on = np.interp(steps, cs[order], cv[order])
    sparse = steps.size < 10  # e.g. a handful of evaluations: compare point by point, no noise estimate
    if sparse:
        r_s, c_s = rv[mask], c_on
        thr = max(min_gap, sparse_gap)
        sustain = 1
    else:
        w = max(3, steps.size // 25)
        r_s = signal.rolling_median(rv[mask], w)
        c_s = signal.rolling_median(c_on, w)
        noise = np.nanmax([_noise(rv), _noise(cv), 0.0])
        thr = max(2.0 * float(noise), min_gap)
        sustain = max(3, steps.size // 20)
    denom = np.maximum(np.abs(r_s), 1e-12)
    gap = (c_s - r_s) / denom
    good = gap if hib is not False else -gap  # + = candidate better (unknown direction: raw difference)
    out = Divergence(run, "same", threshold=thr)
    k = 1 if sparse else max(1, steps.size // 20)
    out.final_gap = float(np.median(good[-k:]))
    out.max_gap = float(good[int(np.argmax(np.abs(good)))])
    outside = np.abs(good) > thr
    if sparse:  # diverged from the first evaluation after which every later one stays outside
        idx = next((i for i in range(steps.size) if outside[i:].all()), None)
    else:
        idx = next((i for i in range(steps.size - sustain + 1) if outside[i : i + sustain].all()), None)
    basis = (
        f" (compared at {steps.size} shared evaluation(s), steps {steps[0]:g}–{steps[-1]:g}; no noise estimate, "
        f"threshold ±{thr:.1%})"
        if sparse
        else ""
    )
    nan_note = ""
    c_bad = ~np.isfinite(np.asarray(cand.values, dtype=float))
    if c_bad.any():
        nan_note = f" {run} turns NaN/Inf from step {np.asarray(cand.steps)[np.argmax(c_bad)]:g}."
    span = float(steps[-1] - steps[0]) or 1.0
    word_better = "better" if hib is not None else "higher"
    word_worse = "worse" if hib is not None else "lower"
    if idx is None:
        out.sentence = (
            f"{run} tracks the reference within ±{thr:.1%} over the shared steps {steps[0]:g}–{steps[-1]:g}"
            f"{basis}.{nan_note}"
        )
        return out
    d_step = float(steps[idx])
    out.diverge_step, out.diverge_fraction = d_step, (d_step - steps[0]) / span
    first_sign = np.sign(good[idx])
    final = out.final_gap
    if abs(final) <= thr:
        out.outcome = "closed"
        tail = f"the gap closes again by the end (final {final:+.1%}, within ±{thr:.1%})"
    elif np.sign(final) != first_sign:
        out.outcome = "crossed"
        tail = (
            f"it first goes {word_better if first_sign > 0 else word_worse}, then crosses and ends "
            f"{abs(final):.1%} {word_better if final > 0 else word_worse}"
        )
    else:
        out.outcome = "ahead" if final > 0 else "behind"
        tail = f"and stays {abs(final):.1%} {word_better if final > 0 else word_worse} to the end"
    out.sentence = (
        f"{run} matches the reference until step {d_step:g} ({out.diverge_fraction:.0%} of the shared run), "
        f"then diverges {tail}."
        if out.outcome in ("ahead", "behind") and idx > 0
        else f"{run} diverges from the reference at step {d_step:g} ({out.diverge_fraction:.0%}); {tail}."
        if idx > 0
        else f"{run} differs from the reference from the first shared step ({d_step:g}) {tail}."
    )
    out.sentence = out.sentence.rstrip(".") + basis + "." + nan_note
    # Learning-rate context at the divergence point.
    near = 0.05 * span
    events = [(e, "reference") for e in _lr_events(ref_lr)] + [(e, run) for e in _lr_events(cand_lr)]
    close = sorted((e for e in events if abs(e[0] - d_step) <= near), key=lambda e: abs(e[0] - d_step))
    a, b = _lr_at(ref_lr, d_step), _lr_at(cand_lr, d_step)
    if close:
        e, who = close[0]
        out.lr_note = f"the {who} LR drops at step {e:g}, right at the divergence point"
    elif a and b and abs(b - a) / a > 0.1:
        out.lr_note = f"at the divergence point the LR differs: reference {_fmt(a)}, {run} {_fmt(b)}"
    # Config changes that fit the timing (hypotheses).
    meaningful = [c for c in changes if not c.is_noise]
    if out.diverge_fraction is not None and out.diverge_fraction < 0.1:
        cats: tuple[str, ...] = EARLY_CATEGORIES + LATE_CATEGORIES
    elif out.lr_note:
        cats = LATE_CATEGORIES + EARLY_CATEGORIES
    else:
        cats = LATE_CATEGORIES + EARLY_CATEGORIES + ("other",)
    ranked = sorted((c for c in meaningful if c.category in cats), key=lambda c: (cats.index(c.category), c.path))
    out.fits = [_change_text(c) for c in ranked[:4]]
    if not meaningful:
        out.fits = ["no config difference — suspect seed variance, code or data changes"]
    return out


@dataclass
class CurvesComparison:
    key: str
    reference: str
    direction: str
    rows: list[Divergence] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "rows": [r.to_dict() for r in self.rows]}


def compare_curves(
    key: str,
    reference: str,
    series: dict[str, MetricSeries],
    *,
    registry: MetricRegistry | None = None,
    lrs: dict[str, MetricSeries | None] | None = None,
    changes: dict[str, tuple[ConfigChange, ...]] | None = None,
    reference_seeds: list[MetricSeries] | None = None,
) -> CurvesComparison:
    """``series`` maps run labels to the metric's series; ``reference`` is one of the labels."""
    hib = _direction(MetricKey.parse(key).name, registry)
    out = CurvesComparison(key, reference, "unknown" if hib is None else ("higher" if hib else "lower"))
    ref = series.get(reference)
    if ref is None:
        out.summary = f"The reference run does not log {key}."
        return out
    lrs = lrs or {}
    changes = changes or {}
    for lab, s in series.items():
        if lab == reference:
            continue
        out.rows.append(
            diverge(
                lab,
                ref,
                s,
                registry=registry,
                ref_lr=lrs.get(reference),
                cand_lr=lrs.get(lab),
                changes=changes.get(lab, ()),
            )
        )
    seeds = [ref, *(reference_seeds or [])]
    finals = [f for f in (_end(x) for x in seeds) if f is not None]
    if len(finals) >= 2:
        mean = float(np.mean(finals))
        std = float(np.std(finals, ddof=1))
        for row in out.rows:
            f = _end(series[row.run])
            if f is None or std <= 0:
                continue
            z = (f - mean) / std
            row.seed_n = len(finals)
            row.seed_z = z if hib is not False else -z
            row.seed_verdict = "beyond seed noise" if abs(z) > 2 else "within seed noise"
            row.sentence += (
                f" Against {len(finals)} reference seeds (mean {_fmt(mean)}, std {_fmt(std)}) the final value is "
                f"{row.seed_z:+.1f}σ — {row.seed_verdict}."
            )
    moved = [r for r in out.rows if r.outcome not in ("same", "insufficient")]
    if not out.rows:
        out.summary = "Only the reference logs this metric."
    elif all(r.outcome == "insufficient" for r in out.rows):
        out.summary = f"Too few shared values to compare {key}."
    elif not moved:
        n = sum(r.outcome == "same" for r in out.rows)
        out.summary = f"{n} run(s) track the reference within the noise on {key}."
    else:
        first = min(moved, key=lambda r: r.diverge_step if r.diverge_step is not None else math.inf)
        out.summary = (
            f"{len(moved)} of {len(out.rows)} run(s) diverge from the reference on {key}; the earliest is "
            f"{first.run} at step {first.diverge_step:g}."
        )
    return out


def _end(s: MetricSeries) -> float | None:
    _, v = s.finite()
    if not v.size:
        return None
    return float(np.median(v[-max(1, v.size // 20) :]))
