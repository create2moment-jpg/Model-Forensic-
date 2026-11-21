"""Training dynamics: divergence, instability, plateau, oscillation, overfitting, train/eval gap."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from model_forensic.analysis import signal
from model_forensic.analysis.base import (
    AnalysisContext,
    AnalysisParams,
    AnalysisResult,
    FindingBuilder,
    PlotSpec,
    Requirement,
    fmt,
)
from model_forensic.domain.findings import Confidence, EpistemicLabel, Severity
from model_forensic.domain.metrics import MetricSeries, Split


class Params(AnalysisParams):
    loss_metric: str = "loss"
    spike_z: float = 8.0
    spike_min_rel: float = 0.4  # a spike must also jump this share above the local (rolling-median) loss
    plateau_tail_frac: float = 0.25
    plateau_min_rel_improvement: float = 0.005
    oscillation_threshold: float = 0.45
    divergence_ratio: float = 3.0
    overfit_min_rise: float = 0.03


class TrainingDynamics:
    name: ClassVar[str] = "training_dynamics"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Training dynamics"
    description: ClassVar[str] = (
        "Detects divergence, non-finite losses, loss spikes, oscillation, late-training plateaus, "
        "overfitting (val loss rising from its minimum while train loss falls) and train/val gap."
    )
    category: ClassVar[str] = "training_dynamics"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Heuristic thresholds; tuned for per-step or per-epoch loss logging with >= 20 points.",
    )
    Params: ClassVar[type[AnalysisParams]] = Params

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, Params) else Params.model_validate(params.model_dump())
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        res = AnalysisResult(analysis=self.name)

        train = ctx.target.find(p.loss_metric, Split.TRAIN.value) or ctx.target.find(p.loss_metric)
        val = ctx.target.find(p.loss_metric, Split.VAL.value)
        if train is None or len(train) < 8:
            res.skipped_reason = f"no '{p.loss_metric}' training series with >= 8 points"
            return res

        res.plots.append(_plot(train, val))
        steps, values = train.arrays()
        key = str(train.key)

        # Non-finite values.
        bad = np.flatnonzero(~np.isfinite(values))
        if bad.size:
            first = float(steps[bad[0]])
            ev = fb.evidence(
                f"{key} is non-finite at {bad.size} of {values.size} points, first at step {first:g}",
                label=EpistemicLabel.OBSERVED,
                run=run.ref,
                metric=key,
                value=first,
            )
            res.findings.append(
                fb.finding(
                    "Non-finite loss (NaN/Inf)",
                    f"Training loss became non-finite at step {first:g}.",
                    severity=Severity.CRITICAL,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.OBSERVED,
                    evidence=[ev],
                    hypotheses=[
                        fb.hypothesis(
                            "Learning rate too high or warmup too short for this batch size",
                            supporting=[ev],
                            falsifiable_by="Re-run with 0.5x LR from the last finite checkpoint",
                        ),
                        fb.hypothesis(
                            "Mixed-precision overflow (fp16) in a loss term",
                            supporting=[ev],
                            falsifiable_by="Re-run the failing window in fp32 / bf16",
                        ),
                    ],
                )
            )
        s, v = train.finite()
        if v.size < 8:
            return res

        # Divergence: late loss far above its minimum.
        # On the smoothed curve: one low noise sample must not become "the minimum" (heavy-tailed noise).
        smooth = signal.rolling_median(v, max(5, v.size // 50))
        min_i = signal.argmin_index(smooth)
        tail_mean = float(np.median(signal.tail(v, 0.1)))
        vmin = float(smooth[min_i])
        if vmin > 0 and tail_mean / vmin >= p.divergence_ratio and min_i < v.size * 0.9:
            ev = fb.evidence(
                f"{key} minimum {fmt(vmin)} at step {s[min_i]:g}; final-10% mean {fmt(tail_mean)} "
                f"({tail_mean / vmin:.1f}x the minimum)",
                run=run.ref,
                metric=key,
                value=tail_mean,
                baseline_value=vmin,
                step_range=(float(s[min_i]), float(s[-1])),
            )
            res.findings.append(
                fb.finding(
                    "Training diverged",
                    f"Loss rose to {tail_mean / vmin:.1f}x its minimum after step {s[min_i]:g}.",
                    severity=Severity.CRITICAL,
                    confidence=Confidence.HIGH,
                    evidence=[ev],
                )
            )

        # Spikes.
        spikes = signal.detect_spikes(s, v, z=p.spike_z, min_rel_jump=p.spike_min_rel)
        if spikes:
            worst = max(spikes, key=lambda sp: sp.robust_z)
            ev = fb.evidence(
                f"{len(spikes)} loss spike(s) > {p.spike_z:g} robust-σ; worst at step {worst.step:g} "
                f"(value {fmt(worst.value)}, {worst.robust_z:.0f}σ)",
                run=run.ref,
                metric=key,
                value=float(len(spikes)),
                data={"spike_steps": [sp.step for sp in spikes[:50]]},
            )
            res.findings.append(
                fb.finding(
                    "Loss instability (spikes)",
                    f"Detected {len(spikes)} loss spike(s); training recovered but the optimizer is near its stability edge.",
                    severity=Severity.MEDIUM if len(spikes) < 5 else Severity.HIGH,
                    confidence=Confidence.MEDIUM,
                    evidence=[ev],
                    hypotheses=[
                        fb.hypothesis(
                            "Insufficient gradient clipping or LR too high",
                            supporting=[ev],
                            falsifiable_by="Check grad_norm around spike steps; rerun with clip_grad 0.5x",
                        )
                    ],
                )
            )

        # Oscillation.
        osc = signal.oscillation_index(v)
        if osc >= p.oscillation_threshold:
            ev = fb.evidence(
                f"Oscillation index of {key} is {osc:.2f} (threshold {p.oscillation_threshold:g})",
                run=run.ref,
                metric=key,
                value=osc,
            )
            res.findings.append(
                fb.finding(
                    "Oscillating loss",
                    "Loss oscillates strongly relative to its trend.",
                    severity=Severity.LOW,
                    confidence=Confidence.MEDIUM,
                    evidence=[ev],
                )
            )

        # Plateau.
        imp = signal.relative_improvement(v, p.plateau_tail_frac)
        if np.isfinite(imp) and abs(imp) < p.plateau_min_rel_improvement:
            ev = fb.evidence(
                f"{key} improved {imp:+.2%} over the last {p.plateau_tail_frac:.0%} of steps",
                run=run.ref,
                metric=key,
                value=imp,
                step_range=(float(signal.tail(s, p.plateau_tail_frac)[0]), float(s[-1])),
            )
            lr = ctx.target.find("lr", Split.TRAIN.value) or ctx.target.find("lr")
            hyps = []
            if lr is not None and len(lr) > 2:
                _, lv = lr.finite()
                ev_lr = fb.evidence(
                    f"Final LR is {fmt(float(lv[-1]))} ({float(lv[-1]) / (float(lv.max()) or 1):.1%} of peak)",
                    label=EpistemicLabel.OBSERVED,
                    run=run.ref,
                    metric=str(lr.key),
                    value=float(lv[-1]),
                )
                hyps.append(
                    fb.hypothesis("Schedule decayed LR too early; extra steps add little", supporting=[ev, ev_lr])
                )
            res.findings.append(
                fb.finding(
                    "Late-training plateau",
                    "Loss is essentially flat in the final phase; additional training budget is likely wasted.",
                    severity=Severity.INFO,
                    confidence=Confidence.MEDIUM,
                    evidence=[ev],
                    hypotheses=hyps,
                )
            )

        # Overfitting / train-val gap.
        if val is not None and len(val) >= 4:
            vs, vv = val.finite()
            vmin_i = int(np.argmin(vv))
            rise = (float(vv[-1]) - float(vv[vmin_i])) / (abs(float(vv[vmin_i])) or 1e-12)
            if rise >= p.overfit_min_rise and vmin_i < vv.size - 1:
                after = s >= vs[vmin_i]
                tr_slope = signal.normalized_slope(s[after], v[after]) if int(np.sum(after)) >= 3 else 0.0
                ev1 = fb.evidence(
                    f"Validation {p.loss_metric} minimum {fmt(float(vv[vmin_i]))} at step {vs[vmin_i]:g}, "
                    f"final {fmt(float(vv[-1]))} ({rise:+.1%})",
                    run=run.ref,
                    metric=str(val.key),
                    value=float(vv[-1]),
                    baseline_value=float(vv[vmin_i]),
                )
                ev2 = fb.evidence(
                    f"Training {p.loss_metric} normalized slope after that step: {tr_slope:+.3f}",
                    run=run.ref,
                    metric=key,
                    value=tr_slope,
                )
                if tr_slope < 0:
                    res.findings.append(
                        fb.finding(
                            "Overfitting after best validation step",
                            f"Validation loss rose {rise:.1%} from its minimum at step {vs[vmin_i]:g} while training "
                            "loss kept falling. The best checkpoint is not the last one.",
                            severity=Severity.HIGH if rise > 0.1 else Severity.MEDIUM,
                            confidence=Confidence.HIGH,
                            evidence=[ev1, ev2],
                            recommendations=[
                                fb.proposal(
                                    "Evaluate the checkpoint nearest the validation minimum",
                                    hypothesis_ids=(),
                                    controlled_variable="checkpoint step",
                                    fixed_variables=("model", "data", "eval protocol"),
                                    expected_outcome="Eval metrics at the min-val-loss checkpoint beat the final checkpoint",
                                    measurements=("headline eval metrics",),
                                    acceptance_criterion="Headline metric improves beyond replicate noise",
                                    estimated_cost_gpu_hours=0.5,
                                    information_gain=0.6,
                                    priority=1,
                                )
                            ],
                        )
                    )
        return res


def _plot(train: MetricSeries, val: MetricSeries | None) -> PlotSpec:
    def pts(ser: MetricSeries, max_points: int = 500) -> list[list[float]]:
        s, v = ser.finite()
        if not s.size:
            return []
        idx = np.linspace(0, s.size - 1, min(max_points, s.size)).astype(int)
        return [[float(s[i]), float(v[i])] for i in idx.tolist()]

    series = [{"name": str(train.key), "points": pts(train)}]
    if val is not None:
        series.append({"name": str(val.key), "points": pts(val)})
    return PlotSpec(id="training_dynamics.loss", title="Loss", y_label="loss", series=series)


PLUGIN = TrainingDynamics()
