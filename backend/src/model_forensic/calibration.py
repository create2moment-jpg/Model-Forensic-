"""Labelled benchmark for the detectors: hits, false alarms and misses per check, and threshold sweeps.

Every scenario is synthetic with a known answer (seeded, deterministic): a loss curve with a real NaN / divergence /
spike / early plateau / still-learning tail / decayed-LR plateau, or a clean curve with realistic noise, warm-up,
LR drops and sparse logging. ``run_benchmark`` reports, per detector, precision and recall at the default threshold
and how they move when the threshold changes; ``markdown`` renders docs/calibration.md (``mf calibrate``).

Teams add their own labelled runs with the feedback buttons ("this finding is wrong / right"); ``feedback_report``
in the API summarises those next to these numbers.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from model_forensic.analysis import study_checks as sc
from model_forensic.analysis.base import AnalysisContext, RunData
from model_forensic.analysis.plugins.training_dynamics import Params as TDParams
from model_forensic.analysis.plugins.training_dynamics import TrainingDynamics
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.run import Run, RunRef, RunStatus

N_PER_SCENARIO = 30


@dataclass
class Scenario:
    name: str
    loss: np.ndarray
    lr: np.ndarray
    truth: set[str]  # detector labels that SHOULD fire


@dataclass
class DetectorScore:
    detector: str
    threshold: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    misses: list[str] = field(default_factory=list)
    false_alarms: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else math.nan

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else math.nan


# ----------------------------------------------------------------------------------------------- scenarios
def _base(rng: np.random.Generator, n: int, tau: float, floor: float, noise: float) -> np.ndarray:
    t = np.arange(n, dtype=float)
    return floor + (2.5 - floor) * np.exp(-t / tau) + rng.normal(0, noise, n) * (0.3 + np.exp(-t / tau))


def _cosine(n: int, peak: float = 8e-4, warmup: int = 0) -> np.ndarray:
    t = np.arange(n, dtype=float)
    lr = peak * 0.5 * (1 + np.cos(math.pi * t / n))
    if warmup:
        lr[:warmup] = peak * (t[:warmup] + 1) / warmup
    return lr


def scenarios(seed: int = 0, n_each: int = N_PER_SCENARIO) -> list[Scenario]:
    rng = np.random.default_rng(seed)
    out: list[Scenario] = []
    for i in range(n_each):
        n = int(rng.integers(300, 1200))
        noise = float(rng.uniform(0.005, 0.03))
        floor = float(rng.uniform(0.2, 0.8))
        # clean: healthy decay with cosine LR (+ warm-up for some) — nothing should fire except "still learning"
        lr = _cosine(n, warmup=int(n * 0.05) if i % 2 else 0)
        loss = _base(rng, n, tau=n / 4, floor=floor, noise=noise)
        out.append(Scenario("clean · cosine", loss, lr, set()))
        # clean with a step LR drop (a legitimate loss drop, not a spike)
        lr = np.full(n, 8e-4)
        lr[int(n * 0.7) :] = 8e-5
        loss = _base(rng, n, tau=n / 6, floor=floor, noise=noise)
        loss[int(n * 0.7) :] -= 0.05
        out.append(Scenario("clean · step LR", loss, lr, set()))
        # clean but noisy with heavy tails (small batches, mixed data): bursts are not pathological spikes
        loss = _base(rng, n, tau=n / 4, floor=floor, noise=0.0)
        loss = loss * (1 + np.clip(rng.standard_t(3, n), -8, 8) * float(rng.uniform(0.02, 0.05)))
        out.append(Scenario("clean · heavy-tailed noise", loss, _cosine(n), set()))
        # NaN
        loss = _base(rng, n, tau=n / 5, floor=floor, noise=noise)
        loss[int(n * rng.uniform(0.3, 0.9)) :] = np.nan
        out.append(Scenario("NaN", loss, _cosine(n), {"nan"}))
        # divergence: loss climbs to many times its minimum
        loss = _base(rng, n, tau=n / 8, floor=floor, noise=noise)
        k = int(n * rng.uniform(0.5, 0.8))
        loss[k:] = loss[k] * np.exp(np.linspace(0, rng.uniform(1.5, 3.0), n - k))
        out.append(Scenario("divergence", loss, np.full(n, 3e-3), {"divergence"}))
        # isolated spikes on an otherwise healthy curve
        loss = _base(rng, n, tau=n / 5, floor=floor, noise=noise)
        for _ in range(int(rng.integers(2, 5))):
            j = int(rng.integers(n // 5, n - 5))
            loss[j] += float(rng.uniform(0.25, 3.0)) * float(np.median(loss))
        out.append(Scenario("spikes", loss, _cosine(n), {"spike"}))
        # early plateau at high LR (converged early): fast decay, constant LR
        loss = _base(rng, n, tau=n / 25, floor=floor, noise=noise * 0.3)
        out.append(Scenario("early plateau · high LR", loss, np.full(n, 8e-4), {"converged_early"}))
        # schedule-bound: plateau arrives when the LR has decayed
        loss = _base(rng, n, tau=n / 25, floor=floor, noise=noise * 0.3)
        lr = _cosine(n)
        lr[int(n * 0.12) :] = lr[int(n * 0.12) :] * 0.05
        out.append(Scenario("plateau · decayed LR", loss, lr, {"schedule_bound"}))
        # still learning: linear-ish decline to the end
        t = np.arange(n, dtype=float)
        loss = 2.0 - 1.2 * t / n + rng.normal(0, noise * 0.3, n)
        out.append(Scenario("still learning", loss, _cosine(n), {"learning"}))
    return out


# ----------------------------------------------------------------------------------------------- detectors
def _series(name: str, y: np.ndarray) -> MetricSeries:
    return MetricSeries(
        key=MetricKey.parse(name), steps=tuple(float(i) for i in range(len(y))), values=tuple(float(v) for v in y)
    )


_RUN = Run(ref=RunRef(backend="bench", run_id="r"), name="r", status=RunStatus.COMPLETED)


def td_labels(s: Scenario, params: TDParams) -> set[str]:
    loss = _series("train/loss", s.loss)
    rd = RunData(run=_RUN, series={loss.key: loss})
    res = TrainingDynamics().run(AnalysisContext(target=rd), params)
    titles = " ".join(f.title.lower() for f in res.findings)
    out = set()
    if "non-finite" in titles:
        out.add("nan")
    if "diverged" in titles:
        out.add("divergence")
    if "spike" in titles:
        out.add("spike")
    return out


def p8_labels(s: Scenario, **kw: Any) -> set[str]:
    finite = np.isfinite(s.loss)
    if not finite.all() or s.name == "divergence":
        return set()
    r = sc.plateau(_series("train/loss", s.loss), _series("train/lr", s.lr), **kw)
    d = r.detail
    return (
        {"converged_early"}
        if "converged early" in d
        else {"schedule_bound"}
        if "schedule-bound" in d
        else {"learning"}
        if "learning" in d
        else set()
    )


def td_fire(params: TDParams) -> Callable[[Scenario], set[str]]:
    return lambda s: td_labels(s, params)


def p8_fire(**kw: Any) -> Callable[[Scenario], set[str]]:
    return lambda s: p8_labels(s, **kw)


def score(
    detector: str, threshold: str, items: Sequence[Scenario], fire: Callable[[Scenario], set[str]], labels: set[str]
) -> DetectorScore:
    out = DetectorScore(detector, threshold)
    for s in items:
        got = fire(s) & labels
        want = s.truth & labels
        for lab in labels:
            if lab in want and lab in got:
                out.tp += 1
            elif lab in want:
                out.fn += 1
                out.misses.append(s.name)
            elif lab in got:
                out.fp += 1
                out.false_alarms.append(s.name)
            else:
                out.tn += 1
    return out


def run_benchmark(seed: int = 0, n_each: int = N_PER_SCENARIO) -> dict[str, Any]:
    items = scenarios(seed, n_each)
    td_default = TDParams()
    results: dict[str, list[DetectorScore]] = {}
    results["training_dynamics · NaN"] = [score("NaN", "default", items, td_fire(td_default), {"nan"})]
    results["training_dynamics · divergence"] = [
        score(
            "divergence",
            f"divergence_ratio={r}",
            items,
            td_fire(TDParams(divergence_ratio=r)),
            {"divergence"},
        )
        for r in (2.0, 3.0, 5.0)
    ]
    results["training_dynamics · spikes"] = [
        score("spikes", f"spike_z={z}", items, td_fire(TDParams(spike_z=z)), {"spike"}) for z in (5.0, 8.0, 12.0)
    ] + [
        score("spikes", f"spike_min_rel={r}", items, td_fire(TDParams(spike_min_rel=r)), {"spike"})
        for r in (0.25, 0.4, 0.6)
    ]
    p8_items = [s for s in items if s.name not in {"NaN", "divergence", "spikes"}]
    for lab, knob, values in (
        ("converged_early", "early_fraction", (0.3, 0.5, 0.7)),
        ("schedule_bound", "schedule_min_remaining", (0.15, 0.25, 0.35, 0.5)),
        ("learning", "learning_gain", (0.01, 0.02, 0.05)),
    ):
        results[f"P8 · {lab.replace('_', ' ')}"] = [
            score(lab, f"{knob}={v}", p8_items, p8_fire(**{knob: v}), {lab}) for v in values
        ]
    defaults = {
        "divergence_ratio": td_default.divergence_ratio,
        "spike_z": td_default.spike_z,
        "spike_min_rel": td_default.spike_min_rel,
        "early_fraction": 0.5,
        "schedule_min_remaining": 0.35,
        "learning_gain": 0.02,
    }
    return {"n_scenarios": len(items), "seed": seed, "results": results, "defaults": defaults}


def markdown(b: dict[str, Any]) -> str:
    lines = [
        "# Calibration",
        "",
        "_Generated by `mf calibrate` (synthetic labelled benchmark, "
        f"{b['n_scenarios']} scenarios, seed {b['seed']}). Do not edit by hand._",
        "",
        "Each detector is scored on scenarios with a known answer: clean curves (noise, warm-up, cosine and step "
        "LR schedules) and curves with a real NaN, divergence, isolated spikes, an early plateau at high LR, a "
        "plateau at a decayed LR, or a still-falling tail. **Precision** = share of alarms that were right; "
        "**recall** = share of real cases caught. The default threshold is in bold.",
        "",
        "| Detector | Threshold | Precision | Recall | False alarms | Misses | Most frequent false alarm |",
        "|---|---|---|---|---|---|---|",
    ]
    defaults = b["defaults"]
    for name, rows in b["results"].items():
        for r in rows:
            knob, _, val = r.threshold.partition("=")
            is_default = r.threshold == "default" or (knob in defaults and float(val) == float(defaults[knob]))
            th = f"**{r.threshold}**" if is_default else r.threshold
            fa = max(set(r.false_alarms), key=r.false_alarms.count) if r.false_alarms else "—"
            p = f"{r.precision:.0%}" if math.isfinite(r.precision) else "—"
            rc = f"{r.recall:.0%}" if math.isfinite(r.recall) else "—"
            lines.append(f"| {name} | {th} | {p} | {rc} | {r.fp} | {r.fn} | {fa} |")
    lines += [
        "",
        "## Tuning",
        "",
        "Thresholds are analysis parameters with these defaults; override them per team in `config/team.local.yaml`:",
        "",
        "```yaml",
        "analysis_params:",
        "  training_dynamics: {spike_z: 8.0, spike_min_rel: 0.4, divergence_ratio: 3.0}",
        "  study_checks: {early_fraction: 0.5, schedule_lr_rel: 0.2, schedule_min_remaining: 0.35, learning_gain: 0.02, degenerate_share: 0.95,",
        "                 muted_sigma: 2.0}",
        "```",
        "",
        "Real-world feedback: the *This is wrong* / *Correct* buttons on every finding store a label locally; "
        "`GET /api/v1/feedback/report` (and Diagnostics) shows the false-alarm rate per analysis from your own runs.",
    ]
    return "\n".join(lines) + "\n"
