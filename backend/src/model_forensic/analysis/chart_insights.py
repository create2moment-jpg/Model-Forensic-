"""'Explain this chart': a deterministic reading of one metric curve, with markers to draw on the chart.

Every statement is computed from the series (and the learning-rate schedule / a baseline when given) and carries the
numbers it is based on, so the same facts can be handed to the AI as citable evidence ("Ask AI about this chart").
Nothing here guesses a cause; causes belong to the analyses and the study.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from model_forensic.analysis import signal
from model_forensic.analysis.metric_registry import MetricRegistry
from model_forensic.domain.metrics import MetricKey, MetricSeries


@dataclass
class Marker:
    step: float
    kind: str  # best | plateau | spike | dip | nan | lr_drop | divergence
    label: str


@dataclass
class ChartReading:
    key: str
    direction: str  # higher | lower | unknown
    points: int
    non_finite: int
    facts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)  # what to look at next, derived from the warnings/facts
    stats: dict[str, float] = field(default_factory=dict)  # numbers behind the facts (for cross-run readings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fmt(v: float) -> str:
    a = abs(v)
    return f"{v:.3e}" if a and (a < 1e-3 or a >= 1e5) else f"{v:.4g}"


def _direction(name: str, registry: MetricRegistry | None) -> bool | None:
    hib = registry.lookup(name).higher_is_better if registry is not None else None
    if hib is None and ("loss" in name or name.endswith(("_err", "error", "rmse", "rsme"))):
        return False
    return hib


def explain(
    series: MetricSeries,
    *,
    registry: MetricRegistry | None = None,
    lr: MetricSeries | None = None,
    baseline: MetricSeries | None = None,
    spike_z: float = 8.0,
    spike_min_rel: float = 0.4,
) -> ChartReading:
    name = series.key.name
    hib = _direction(name, registry)
    steps_all = np.asarray(series.steps, dtype=float)
    vals_all = np.asarray(series.values, dtype=float)
    bad = ~np.isfinite(vals_all)
    out = ChartReading(
        str(series.key),
        "unknown" if hib is None else ("higher" if hib else "lower"),
        int(vals_all.size),
        int(bad.sum()),
    )
    if bad.any():
        first = float(steps_all[np.argmax(bad)])
        out.warnings.append(f"{int(bad.sum())} non-finite value(s) (NaN/Inf); the first at step {first:g}.")
        out.markers.append(Marker(first, "nan", "first NaN/Inf"))
        out.stats["first_nan_step"] = first
        out.checks.append(
            f"Inspect the steps just before {first:g}: the batch, gradient norm and LR there; re-run that window in "
            "fp32/bf16 to rule out a mixed-precision overflow."
        )
    s, v = series.finite()
    if v.size:
        out.stats["last"] = float(v[-1])
        out.stats["final"] = float(np.median(v[-max(3, v.size // 20) :]))  # robust end value (last 5 %)
    if v.size == 0:
        out.warnings.append("No finite values to read.")
        return out
    if v.size == 1:
        out.facts.append(f"A single value: {_fmt(float(v[0]))} at step {s[0]:g}.")
        return out

    def better(a: float, b: float) -> bool:
        return a > b if hib is not False else a < b

    # Trend: first vs last decile (medians, robust to noise).
    k = max(1, v.size // 10)
    head, tail = float(np.median(v[:k])), float(np.median(v[-k:]))
    if head != 0:
        rel = (tail - head) / abs(head)
        out.stats["trend_rel"] = rel
        word = "rose" if rel > 0 else "fell"
        verdict = ""
        if hib is not None and abs(rel) > 0.01:
            verdict = " (better)" if better(tail, head) else " (worse)"
        out.facts.append(
            f"From {_fmt(head)} at the start to {_fmt(tail)} at the end: it {word} by {abs(rel):.1%}{verdict}."
        )
    # Best value.
    bi = int(np.argmax(v) if hib else np.argmin(v)) if hib is not None else None
    if bi is not None:
        best, last = float(v[bi]), float(v[-1])
        out.stats.update(best=best, best_step=float(s[bi]), last=last)
        frac = (s[bi] - s[0]) / (s[-1] - s[0]) if s[-1] != s[0] else 1.0
        out.facts.append(
            f"Best {_fmt(best)} at step {s[bi]:g} ({frac:.0%} of the run); the last value is {_fmt(last)}."
        )
        out.markers.append(Marker(float(s[bi]), "best", f"best {_fmt(best)}"))
        if best != 0 and frac < 0.9:
            gap = abs(last - best) / abs(best)
            if gap > 0.02:
                out.warnings.append(
                    f"The last value is {gap:.1%} {'below' if hib else 'above'} the best — the final checkpoint is "
                    "not the best one."
                )
                out.checks.append(
                    f"Make sure checkpoint selection monitors a logged metric (the best is at step {s[bi]:g}), or "
                    "evaluate that checkpoint instead of the last."
                )
    # Plateau (only for curves that should improve).
    if hib is not None and v.size >= 20:
        seg = 10
        edges = np.linspace(s.min(), s.max(), seg + 1)
        means = [float(np.mean(v[(s >= a) & (s <= b)])) for a, b in itertools.pairwise(edges)]
        sign = 1.0 if hib else -1.0
        gain = sign * (means[-1] - means[0])
        if gain > 0:
            idx = next(i for i in range(seg) if sign * (means[-1] - means[i]) < 0.05 * gain)
            if 0 < idx < seg - 1:
                at = float(edges[idx])
                out.stats["plateau_step"] = at
                out.facts.append(
                    f"95% of the improvement is reached by step {at:g} ({idx / seg:.0%} of the run); after that it is flat."
                )
                out.markers.append(Marker(at, "plateau", "plateau starts"))
                if hib is False and idx / seg < 0.5:
                    out.checks.append(
                        "Most of the run adds little: compare the LR at the plateau with its peak (Convergence / P8) "
                        "before deciding to train longer."
                    )
        elif abs(gain) > 0.02 * max(abs(means[0]), 1e-12):
            out.warnings.append("Over the whole run the curve got worse, not better.")
            out.checks.append("Find where it turned (the best marker) and compare the config/LR before and after it.")
    # Spikes (loss-like) or dips (score-like).
    if hib is not None:
        # Dips of a score are spikes of its mirror image (same level, so the relative jump stays meaningful).
        y = v if hib is False else float(v.max() + v.min()) - v
        spikes = signal.detect_spikes(s, y, z=spike_z, min_rel_jump=spike_min_rel)
        if spikes:
            kind = "spike" if hib is False else "dip"
            out.warnings.append(
                f"{len(spikes)} isolated {kind}(s) far outside the local noise; the largest at step "
                f"{max(spikes, key=lambda sp: sp.robust_z).step:g}."
            )
            out.checks.append(
                "Look at the gradient norm and the data at the marked steps; frequent spikes usually mean the LR is near "
                "its stability edge or gradient clipping is too loose."
                if kind == "spike"
                else "Check which evaluation slices drop at the marked steps (a bad checkpoint or an evaluation glitch)."
            )
            for sp in sorted(spikes, key=lambda sp: -sp.robust_z)[:5]:
                out.markers.append(Marker(sp.step, kind, f"{kind} {_fmt(float(v[sp.index]))}"))
    # Noise level in the second half.
    half = v[v.size // 2 :]
    noise = math.nan
    if half.size >= 10 and not bad.any():
        resid = half - signal.rolling_median(half, max(5, half.size // 20))
        level = float(np.median(np.abs(half))) or 1.0
        noise = signal.mad(resid) / level
        out.stats["noise_rel"] = noise
        if math.isfinite(noise):
            out.facts.append(f"Step-to-step noise in the second half: ±{noise:.1%} of the level.")
    # Learning-rate drops (step schedules).
    if lr is not None and len(lr) >= 2:
        ls, lv = lr.finite()
        drops = [i for i in range(1, lv.size) if lv[i - 1] > 0 and lv[i] < 0.5 * lv[i - 1]]
        for i in drops[:5]:
            out.markers.append(Marker(float(ls[i]), "lr_drop", f"LR {_fmt(float(lv[i - 1]))} → {_fmt(float(lv[i]))}"))
        if drops:
            out.facts.append(
                f"The learning rate drops {len(drops)} time(s) (first at step {ls[drops[0]]:g}); look for a matching "
                "step in the curve."
            )
        elif lv.size:
            peak = float(lv.max())
            out.facts.append(f"Learning rate ends at {float(lv[-1]) / peak:.0%} of its peak {_fmt(peak)}.")
    # Baseline.
    if baseline is not None and len(baseline):
        _, bv = baseline.finite()
        if bv.size and bv[-1] != 0:
            d = (float(v[-1]) - float(bv[-1])) / abs(float(bv[-1]))
            tag = ""
            if math.isfinite(noise) and abs(d) <= 2 * noise:
                tag = " (within the step-to-step noise)"
            elif hib is not None and abs(d) > 0.005:
                tag = " (better)" if better(float(v[-1]), float(bv[-1])) else " (worse)"
            out.facts.append(f"Final value vs the baseline's {_fmt(float(bv[-1]))}: {d:+.1%}{tag}.")
    return out


@dataclass
class RunsReading:
    key: str
    direction: str
    runs: int
    facts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    ranking: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def explain_runs(
    key: str,
    series: dict[str, MetricSeries],
    *,
    registry: MetricRegistry | None = None,
    running: set[str] | frozenset[str] = frozenset(),
) -> RunsReading:
    """Cross-run reading of one metric: ranking by final value, spread vs run-to-run noise, NaN / diverged runs,
    which runs flatten first and which are still improving."""
    name = MetricKey.parse(key).name
    hib = _direction(name, registry)
    out = RunsReading(key, "unknown" if hib is None else ("higher" if hib else "lower"), len(series))
    per = {lab: explain(s, registry=registry) for lab, s in series.items()}
    finals = {
        lab: r.stats["final"]
        for lab, r in per.items()
        if "final" in r.stats and not r.non_finite and lab not in running
    }
    live = sorted(lab for lab in per if lab in running)
    if live:
        out.facts.append(f"Still running, not ranked: {', '.join(live[:6])}.")
    nan_runs = sorted(lab for lab, r in per.items() if r.non_finite)
    if nan_runs:
        out.warnings.append(
            f"{len(nan_runs)} run(s) hit NaN/Inf: "
            + ", ".join(f"{lab} (step {per[lab].stats.get('first_nan_step', float('nan')):g})" for lab in nan_runs[:6])
            + "."
        )
        out.checks.append("Open the NaN runs and compare their config with a healthy run (Compare → config diff).")
    worse_runs = sorted(
        lab for lab, r in per.items() if lab not in nan_runs and any("got worse" in w for w in r.warnings)
    )
    if worse_runs:
        out.warnings.append(f"Got worse over the run: {', '.join(worse_runs[:6])}.")
    if len(finals) >= 2 and hib is not None:
        order = sorted(finals, key=lambda lab: finals[lab], reverse=bool(hib))
        out.ranking = [
            {
                "run": lab,
                "final": finals[lab],
                "best": per[lab].stats.get("best"),
                "plateau_step": per[lab].stats.get("plateau_step"),
            }
            for lab in order
        ]
        top, bottom = order[0], order[-1]
        spread = abs(finals[top] - finals[bottom]) / max(abs(finals[bottom]), 1e-12)
        out.facts.append(
            f"Best final value (median of the last 5% of points): {top} {_fmt(finals[top])}; worst: {bottom} "
            f"{_fmt(finals[bottom])} "
            f"(spread {spread:.1%} across {len(finals)} runs)."
        )
        noises = [r.stats["noise_rel"] for r in per.values() if "noise_rel" in r.stats]
        if noises:
            noise = float(np.median(noises))
            if len(order) >= 2:
                gap12 = abs(finals[order[0]] - finals[order[1]]) / max(abs(finals[order[1]]), 1e-12)
                if gap12 <= 2 * noise:
                    out.facts.append(
                        f"The top two ({order[0]}, {order[1]}) differ by {gap12:.1%} — within the typical step-to-step "
                        f"noise (±{noise:.1%}); their order is not meaningful."
                    )
                    out.checks.append("Add seed replicates of the top runs (same `group` tag) before picking a winner.")
            if spread <= 2 * noise:
                out.warnings.append(
                    f"All runs end within the step-to-step noise (±{noise:.1%}): this metric does not separate them."
                )
    plats = {lab: r.stats["plateau_step"] for lab, r in per.items() if "plateau_step" in r.stats and lab not in running}
    if len(plats) >= 2:
        first = min(plats, key=lambda lab: plats[lab])
        last = max(plats, key=lambda lab: plats[lab])
        out.facts.append(f"Flattens first: {first} (step {plats[first]:g}); last: {last} (step {plats[last]:g}).")
    still = sorted(
        lab
        for lab, r in per.items()
        if lab in finals
        and "plateau_step" not in r.stats
        and r.stats.get("trend_rel") is not None
        and lab not in worse_runs
    )
    if still and hib is not None:
        out.facts.append(f"Still improving at the end (no plateau): {', '.join(still[:6])}.")
    if not out.checks and out.ranking:
        out.checks.append(
            f"Compare the best run ({out.ranking[0]['run']}) with the worst ({out.ranking[-1]['run']}): the config "
            "diff names the candidate cause."
        )
    return out
