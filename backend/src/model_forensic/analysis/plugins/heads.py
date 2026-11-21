"""Analyses for uncertainty heads, streaming temporal memory and loss-term interactions.

Inputs come from the instrumentation kit:
* ``uncertainty_stats``      → ``val|train/unc_{abs_err,scale,nerr,cov,corr,clamped_frac}[dim=…(,range=…)]``
* ``log_losses(seqpos=…)``    → ``train/loss/<term>[seqpos=0|1-3|4-inf]``; eval ``val/<metric>[seqpos=…]``
* ``log_query_sources``      → ``train/q_pos_frac[source=propagated|new]``, ``train/q_from_memory_frac``
* ``LossGradProbe``          → ``train/term_grad_norm[term=…]``, ``train/term_grad_cos[a=…,b=…]``
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import ClassVar

from model_forensic.analysis.base import (
    AnalysisContext,
    AnalysisParams,
    AnalysisResult,
    FindingBuilder,
    PlotSpec,
    Requirement,
    RunData,
    fmt,
)
from model_forensic.analysis.plugins.architecture import KIT_HINT, _early, _tail_mean
from model_forensic.domain.findings import Confidence, EpistemicLabel, Evidence, Severity
from model_forensic.domain.metrics import MetricSeries, Split


def _collect(data: RunData, name: str, split: Split | None = None) -> dict[tuple[tuple[str, str], ...], MetricSeries]:
    return {k.dims: s for k, s in data.series.items() if k.name == name and (split is None or k.split == split)}


# --------------------------------------------------------------------------------------------- uncertainty
class UncertaintyParams(AnalysisParams):
    overconfident_ratio: float = 1.3
    underconfident_ratio: float = 0.7
    min_rank_corr: float = 0.1
    clamped_frac: float = 0.2
    attenuation_scale_growth: float = 1.3
    range_gap: float = 1.5


class UncertaintyHealth:
    name: ClassVar[str] = "uncertainty_health"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Uncertainty head health"
    description: ClassVar[str] = (
        "Calibration of predicted regression uncertainty (Laplace/Gaussian NLL heads): over/under-confidence per box "
        "dimension and range, whether uncertainty ranks errors, clamp saturation, and loss attenuation — the model "
        "inflating its uncertainty instead of reducing the error."
    )
    category: ClassVar[str] = "uncertainty"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Calibration is measured on matched objects only; false positives' uncertainty is not covered.",
    )
    Params: ClassVar[type[AnalysisParams]] = UncertaintyParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, UncertaintyParams) else UncertaintyParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        split = Split.VAL if _collect(ctx.target, "unc_nerr", Split.VAL) else Split.TRAIN
        nerr = _collect(ctx.target, "unc_nerr", split)
        if not nerr:
            res.skipped_reason = f"no uncertainty diagnostics (val/unc_nerr[dim=…]). {KIT_HINT}"
            return res

        def final(name: str, dims: tuple[tuple[str, str], ...], sp: Split = split) -> float | None:
            s = _collect(ctx.target, name, sp).get(dims)
            return _tail_mean(s, 0.2) if s is not None else None

        per_dim = {dict(d)["dim"]: d for d in nerr if set(dict(d)) == {"dim"}}
        over, under, flat, clamped = [], [], [], []
        evs: list[Evidence] = []
        for dim, dims in sorted(per_dim.items()):
            r = final("unc_nerr", dims)
            cov = final("unc_cov", tuple(sorted((*dims, ("level", "68")))))
            corr = final("unc_corr", dims)
            cl = final("unc_clamped_frac", dims)
            if r is None:
                continue
            evs.append(
                fb.evidence(
                    f"{dim}: calibration ratio {r:.2f} (1.0 = calibrated)"
                    + (f", 68% interval covers {cov:.0%}" if cov is not None else "")
                    + (f", rank corr {corr:.2f}" if corr is not None else "")
                    + (f", {cl:.0%} at clamp" if cl is not None else ""),
                    run=run.ref,
                    metric=f"{split.value}/unc_nerr[dim={dim}]",
                    value=r,
                    data={"coverage68": cov, "corr": corr},
                )
            )
            if r > p.overconfident_ratio:
                over.append((dim, r))
            elif r < p.underconfident_ratio:
                under.append((dim, r))
            if corr is not None and corr < p.min_rank_corr:
                flat.append((dim, corr))
            if cl is not None and cl > p.clamped_frac:
                clamped.append((dim, cl))
        res.plots.append(
            PlotSpec(
                id="uncertainty.calibration",
                title="Calibration ratio per box dimension (1 = calibrated)",
                kind="bar",
                x_label="dimension",
                y_label="error / predicted scale",
                series=[{"name": "ratio", "points": [[d, final("unc_nerr", per_dim[d])] for d in sorted(per_dim)]}],
            )
        )
        if over:
            res.findings.append(
                fb.finding(
                    f"Uncertainty is over-confident for {', '.join(d for d, _ in over)}",
                    "Actual errors are larger than the predicted uncertainty ("
                    + ", ".join(f"{d} {r:.1f}×" for d, r in over)
                    + "); downstream consumers (tracking, planning) will trust these boxes too much.",
                    severity=Severity.HIGH if max(r for _, r in over) > 2 else Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=evs,
                    hypotheses=[
                        fb.hypothesis(
                            "The uncertainty loss weight is too small relative to the box loss", supporting=evs[:1]
                        ),
                        fb.hypothesis("Scale is clamped too low (check clamp settings)", supporting=evs[:1]),
                    ],
                    key="over",
                )
            )
        if under:
            res.findings.append(
                fb.finding(
                    f"Uncertainty is under-confident for {', '.join(d for d, _ in under)}",
                    "Predicted uncertainty is larger than the actual errors ("
                    + ", ".join(f"{d} {r:.2f}×" for d, r in under)
                    + ").",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=evs,
                    key="under",
                )
            )
        if flat:
            res.findings.append(
                fb.finding(
                    f"Uncertainty does not rank errors for {', '.join(d for d, _ in flat)}",
                    "The predicted scale is barely correlated with the actual error ("
                    + ", ".join(f"{d} ρ={c:.2f}" for d, c in flat)
                    + "): it cannot tell good boxes from bad ones.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=evs,
                    hypotheses=[
                        fb.hypothesis(
                            "The uncertainty branch lacks the features that predict error (e.g. range, occlusion)",
                            supporting=evs[:1],
                        )
                    ],
                    key="flat",
                )
            )
        if clamped:
            res.findings.append(
                fb.finding(
                    f"Uncertainty saturates at its clamp for {', '.join(d for d, _ in clamped)}",
                    "A large share of predicted scales sit at the clamp bound, so the head cannot express more/less uncertainty there.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    evidence=evs,
                    key="clamp",
                )
            )

        # Per range: is calibration much worse far away?
        by_range: dict[str, dict[str, float]] = defaultdict(dict)
        for dims in nerr:
            d = dict(dims)
            if set(d) == {"dim", "range"}:
                v = final("unc_nerr", dims)
                if v is not None:
                    by_range[d["dim"]][d["range"]] = v
        far_bad: list[tuple[str, str, float, Evidence]] = []
        for dim, vals in sorted(by_range.items()):
            if len(vals) >= 2:
                lo_r, hi_r = min(vals, key=lambda k: vals[k]), max(vals, key=lambda k: vals[k])
                if vals[lo_r] > 0 and vals[hi_r] / vals[lo_r] > p.range_gap and vals[hi_r] > p.overconfident_ratio:
                    ev = fb.evidence(
                        f"{dim} calibration ratio by range: "
                        + ", ".join(f"{k} {v:.2f}" for k, v in sorted(vals.items())),
                        run=run.ref,
                        metric=f"{split.value}/unc_nerr[dim={dim},range=*]",
                        data={"by_range": vals},
                    )
                    far_bad.append((dim, hi_r, vals[hi_r] / vals[lo_r], ev))
        if far_bad:
            where = sorted({h for _, h, _, _ in far_bad})
            res.findings.append(
                fb.finding(
                    f"Uncertainty is over-confident at {', '.join(where)} ({', '.join(d for d, _, _, _ in far_bad)})",
                    "Calibration degrades with range ("
                    + ", ".join(f"{d} {g:.1f}× worse at {h}" for d, h, g, _ in far_bad)
                    + "): the head does not raise its uncertainty enough where errors grow.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[e for *_, e in far_bad],
                    hypotheses=[
                        fb.hypothesis(
                            "The uncertainty branch has no access to range/point-density cues",
                            supporting=[far_bad[0][3]],
                            falsifiable_by="Add range (or point count) as an input to the uncertainty branch",
                        )
                    ],
                    key="range",
                )
            )

        # Loss attenuation over training: scale grows while error does not shrink.
        tscale, terr = _collect(ctx.target, "unc_scale", Split.TRAIN), _collect(ctx.target, "unc_abs_err", Split.TRAIN)
        if not tscale:
            tscale, terr = _collect(ctx.target, "unc_scale", Split.VAL), _collect(ctx.target, "unc_abs_err", Split.VAL)
        att = []
        for dims, s in tscale.items():
            d = dict(dims)
            if set(d) != {"dim"} or dims not in terr:
                continue
            se, sl = _early(s, 0.2), _tail_mean(s, 0.2)
            ee, el = _early(terr[dims], 0.2), _tail_mean(terr[dims], 0.2)
            if se > 0 and ee > 0 and sl / se > p.attenuation_scale_growth and el / ee > 0.9:
                att.append((d["dim"], sl / se, el / ee))
        if att:
            ev = fb.evidence(
                "Over training: " + "; ".join(f"{d}: predicted scale ×{g:.1f}, error ×{e:.2f}" for d, g, e in att),
                run=run.ref,
                metric="train/unc_scale[dim=*]",
                data={"attenuation": {d: [g, e] for d, g, e in att}},
            )
            res.findings.append(
                fb.finding(
                    f"Loss attenuation on {', '.join(d for d, _, _ in att)}",
                    "The model reduces the NLL by inflating its predicted uncertainty while the actual error does not improve — the "
                    "uncertainty term is absorbing the regression loss instead of the box getting better.",
                    severity=Severity.HIGH,
                    confidence=Confidence.MEDIUM,
                    evidence=[ev],
                    hypotheses=[
                        fb.hypothesis(
                            "The NLL term lets hard examples be down-weighted (attenuation) — the regression signal is lost",
                            supporting=[ev],
                            falsifiable_by="Detach the scale in the regression term, or add a plain L1 term; error should fall",
                        )
                    ],
                    recommendations=[
                        fb.proposal(
                            "Keep a plain regression term next to the NLL",
                            hypothesis_ids=(),
                            controlled_variable="regression loss formulation",
                            fixed_variables=("architecture", "data", "schedule"),
                            expected_outcome="Box error on the attenuated dimensions decreases; calibration stays close to 1",
                            measurements=("unc_abs_err", "mATE / mAVE", "unc_nerr"),
                            acceptance_criterion="Error down beyond seed noise",
                            estimated_cost_gpu_hours=run.gpu_hours,
                            information_gain=0.7,
                            priority=1,
                        )
                    ],
                    key="attenuation",
                )
            )
        if not res.findings:
            res.notes.append("Uncertainty looks calibrated and informative.")
        return res


# --------------------------------------------------------------------------------------------- temporal memory
class TemporalParams(AnalysisParams):
    min_benefit: float = 0.02
    min_memory_share: float = 0.10


class TemporalMemory:
    name: ClassVar[str] = "temporal_memory"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Temporal memory benefit"
    description: ClassVar[str] = (
        "For streaming / temporal models (StreamPETR-style memory queues): are frames with history actually better than "
        "the first frame of a sequence (loss and eval by sequence position), and are propagated memory queries used?"
    )
    category: ClassVar[str] = "temporal"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Sequence position is confounded with scene content at the start of sequences; compare runs on the same data.",
    )
    Params: ClassVar[type[AnalysisParams]] = TemporalParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, TemporalParams) else TemporalParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        groups: dict[tuple[str, str], dict[str, MetricSeries]] = defaultdict(dict)  # (split, name) → seqpos → series
        for k, s in ctx.target.series.items():
            d = k.dim_dict
            if set(d) == {"seqpos"}:
                groups[(k.split.value, k.name)][d["seqpos"]] = s
        q_mem = _collect(ctx.target, "q_from_memory_frac", Split.TRAIN).get(())
        q_src = {
            dict(d)["source"]: s
            for d, s in _collect(ctx.target, "q_pos_frac", Split.TRAIN).items()
            if set(dict(d)) == {"source"}
        }
        if not groups and q_mem is None:
            res.skipped_reason = (
                f"no sequence-position metrics (…[seqpos=0|1-3|4-inf]) or query-source stats. {KIT_HINT}"
            )
            return res

        evs: list[Evidence] = []
        benefits: dict[str, float] = {}
        for (split, name), by_pos in sorted(groups.items()):
            if "0" not in by_pos or len(by_pos) < 2:
                continue
            far = sorted((k for k in by_pos if k != "0"), key=lambda k: int(k.split("-")[0]))[-1]
            v0, vf = _tail_mean(by_pos["0"], 0.3), _tail_mean(by_pos[far], 0.3)
            if not (math.isfinite(v0) and math.isfinite(vf)) or v0 == 0:
                continue
            hib = ctx.registry.lookup(name).higher_is_better
            benefit = (vf - v0) / abs(v0) if hib else (v0 - vf) / abs(v0)
            key = f"{split}/{name}"
            benefits[key] = benefit
            evs.append(
                fb.evidence(
                    f"{key}: no history (seqpos 0) {fmt(v0, 4)} vs history {far} {fmt(vf, 4)} → "
                    f"{benefit:+.1%} from temporal memory",
                    run=run.ref,
                    metric=f"{key}[seqpos=*]",
                    value=vf,
                    baseline_value=v0,
                )
            )
        if benefits:
            main = next(
                (benefits[k] for k in ("val/NDS", "train/loss") if k in benefits), next(iter(benefits.values()))
            )
            res.plots.append(
                PlotSpec(
                    id="temporal.benefit",
                    title="Gain of frames with history over first frames",
                    kind="bar",
                    x_label="metric",
                    y_label="relative gain",
                    series=[{"name": "gain", "points": [[k, v] for k, v in benefits.items()]}],
                )
            )
            if main < 0:
                res.findings.append(
                    fb.finding(
                        "Frames with temporal history are worse than first frames",
                        f"With memory the model does {-main:.1%} worse than without it: temporal fusion is hurting.",
                        severity=Severity.HIGH,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=evs,
                        hypotheses=[
                            fb.hypothesis(
                                "Ego-motion alignment of memory queries is wrong (pose/timestamp convention)",
                                supporting=evs[:1],
                            ),
                            fb.hypothesis(
                                "Memory is not reset at sequence boundaries (stale objects)", supporting=evs[:1]
                            ),
                        ],
                        key="negative",
                    )
                )
            elif main < p.min_benefit:
                res.findings.append(
                    fb.finding(
                        "Temporal memory barely helps",
                        f"Frames with history are only {main:.1%} better than the first frame of a sequence.",
                        severity=Severity.HIGH,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=evs,
                        hypotheses=[
                            fb.hypothesis(
                                "Propagated queries are not matched (check query-source stats)", supporting=evs[:1]
                            ),
                            fb.hypothesis(
                                "Memory length / number of propagated queries is too small", supporting=evs[:1]
                            ),
                        ],
                        key="small",
                    )
                )
            else:
                best = max(benefits, key=lambda k: benefits[k])
                res.findings.append(
                    fb.finding(
                        f"Temporal memory gives {main:.1%}",
                        f"Frames with history are {main:.1%} better; the largest gain is in {best} "
                        f"({benefits[best]:+.1%}).",
                        severity=Severity.INFO,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=evs,
                        key="gain",
                    )
                )
        if q_mem is not None:
            share = _tail_mean(q_mem, 0.3)
            ev = fb.evidence(
                f"{share:.0%} of matched queries come from the temporal memory",
                run=run.ref,
                metric="train/q_from_memory_frac",
                value=share,
            )
            qe = [ev]
            if "propagated" in q_src and "new" in q_src:
                pp, pn = _tail_mean(q_src["propagated"], 0.3), _tail_mean(q_src["new"], 0.3)
                qe.append(
                    fb.evidence(
                        f"Positive rate: propagated queries {pp:.1%}, new queries {pn:.1%}",
                        run=run.ref,
                        metric="train/q_pos_frac[source=*]",
                        value=pp,
                        baseline_value=pn,
                    )
                )
                if pp < pn:
                    res.findings.append(
                        fb.finding(
                            "Propagated memory queries are worse candidates than fresh ones",
                            f"Only {pp:.1%} of propagated queries are matched vs {pn:.1%} of new ones: memory slots are spent on "
                            "queries that rarely become detections.",
                            severity=Severity.MEDIUM,
                            confidence=Confidence.MEDIUM,
                            label=EpistemicLabel.COMPUTED,
                            evidence=qe,
                            hypotheses=[
                                fb.hypothesis(
                                    "Top-k selection for propagation uses scores that do not reflect objectness",
                                    supporting=qe,
                                )
                            ],
                            key="prop_worse",
                        )
                    )
            if share < p.min_memory_share:
                res.findings.append(
                    fb.finding(
                        "Memory queries are rarely matched",
                        f"Only {share:.0%} of matched queries come from memory: tracking-by-query is effectively off.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=qe,
                        key="mem_share",
                    )
                )
        if not res.findings:
            res.notes.append("Temporal memory diagnostics look normal.")
        return res


# --------------------------------------------------------------------------------------------- loss interactions
class InteractionParams(AnalysisParams):
    conflict_cos: float = -0.1
    strong_conflict_cos: float = -0.3
    dominant_share: float = 0.7
    negligible_share: float = 0.01


class LossInteractions:
    name: ClassVar[str] = "loss_interactions"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Loss-term interactions"
    description: ClassVar[str] = (
        "From the loss-gradient probe: which loss terms actually steer the shared module (gradient share vs loss-value "
        "share) and which pairs pull it in opposite directions (negative gradient cosine)."
    )
    category: ClassVar[str] = "loss"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = ("Measured on one shared module at sparse probe steps.",)
    Params: ClassVar[type[AnalysisParams]] = InteractionParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, InteractionParams) else InteractionParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        norms = {
            dict(d)["term"]: _tail_mean(s, 0.3)
            for d, s in _collect(ctx.target, "term_grad_norm", Split.TRAIN).items()
            if set(dict(d)) == {"term"}
        }
        coss = {
            (dict(d)["a"], dict(d)["b"]): s
            for d, s in _collect(ctx.target, "term_grad_cos", Split.TRAIN).items()
            if set(dict(d)) == {"a", "b"}
        }
        if not norms:
            res.skipped_reason = f"no loss-gradient probe metrics (train/term_grad_norm[term=…]). {KIT_HINT}"
            return res
        total = sum(norms.values()) or 1.0
        gshare = {t: v / total for t, v in norms.items()}
        lvals = {t: _tail_mean(s, 0.3) for t in norms if (s := ctx.target.find(f"loss/{t}", "train")) is not None}
        ltotal = sum(abs(v) for v in lvals.values()) or 1.0
        table = fb.evidence(
            "Share of gradient on the shared module vs share of loss value: "
            + "; ".join(
                f"{t} {gshare[t]:.0%} vs {abs(lvals[t]) / ltotal:.0%}" if t in lvals else f"{t} {gshare[t]:.0%}"
                for t in sorted(gshare)
            ),
            run=run.ref,
            metric="train/term_grad_norm[term=*]",
            data={"grad_share": gshare},
        )
        res.plots.append(
            PlotSpec(
                id="loss_interactions.share",
                title="Gradient share on the shared module",
                kind="bar",
                x_label="term",
                y_label="share",
                series=[{"name": "share", "points": [[t, v] for t, v in sorted(gshare.items())]}],
            )
        )
        top = max(gshare, key=lambda t: gshare[t])
        if gshare[top] > p.dominant_share and len(gshare) > 1:
            lv = abs(lvals[top]) / ltotal if top in lvals else None
            res.findings.append(
                fb.finding(
                    f"{top} dominates the shared gradient ({gshare[top]:.0%})",
                    f"{top} supplies {gshare[top]:.0%} of the gradient on the shared module"
                    + (
                        f" while being only {lv:.0%} of the loss value"
                        if lv is not None and lv < gshare[top] / 2
                        else ""
                    )
                    + ": the other terms barely shape the shared features.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    evidence=[table],
                    hypotheses=[
                        fb.hypothesis(
                            f"Loss weights balance values, not gradients; {top} needs a lower weight",
                            supporting=[table],
                        )
                    ],
                    key=f"dom|{top}",
                )
            )
        for t, sh in sorted(gshare.items()):
            if sh < p.negligible_share:
                res.findings.append(
                    fb.finding(
                        f"{t} barely affects the shared module",
                        f"{t} provides {sh:.1%} of the shared gradient.",
                        severity=Severity.LOW,
                        confidence=Confidence.MEDIUM,
                        evidence=[table],
                        key=f"neg|{t}",
                    )
                )
        for (a, b), s in sorted(coss.items()):
            c = median(s.finite()[1].tolist()[-max(3, len(s) // 3) :]) if len(s) else math.nan
            if math.isfinite(c) and c < p.conflict_cos:
                ev = fb.evidence(
                    f"Gradient cosine between {a} and {b} on the shared module: median {c:+.2f} late in training",
                    run=run.ref,
                    metric=f"train/term_grad_cos[a={a},b={b}]",
                    value=c,
                )
                res.findings.append(
                    fb.finding(
                        f"{a} and {b} pull the shared module in opposite directions",
                        f"The gradients of {a} and {b} point against each other (cosine {c:+.2f}); improving one costs the other.",
                        severity=Severity.HIGH if c < p.strong_conflict_cos else Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=[ev, table],
                        hypotheses=[
                            fb.hypothesis(
                                f"{a} and {b} need separate branches/heads instead of shared features", supporting=[ev]
                            ),
                            fb.hypothesis(
                                "Relative loss weights push the shared features toward one objective", supporting=[ev]
                            ),
                        ],
                        recommendations=[
                            fb.proposal(
                                f"Decouple {a} and {b}",
                                hypothesis_ids=(),
                                controlled_variable=f"head structure / loss weights for {a}, {b}",
                                fixed_variables=("everything else",),
                                expected_outcome="Cosine closer to 0; both terms and their metrics improve",
                                measurements=(f"train/term_grad_cos[a={a},b={b}]", "headline metrics"),
                                acceptance_criterion="Metrics of both objectives improve beyond seed noise",
                                estimated_cost_gpu_hours=run.gpu_hours,
                                information_gain=0.5,
                                priority=2,
                            )
                        ],
                        key=f"conflict|{a}|{b}",
                    )
                )
        if not res.findings:
            res.notes.append("No dominant, negligible or conflicting loss terms on the probed module.")
        return res


UNCERTAINTY_HEALTH = UncertaintyHealth()
TEMPORAL_MEMORY = TemporalMemory()
LOSS_INTERACTIONS = LossInteractions()


# --------------------------------------------------------------------------------------------- learned task weights
class TaskWeightingParams(AnalysisParams):
    sigma_move: float = 0.5  # |Δ log σ| at which a head's effective weight changed materially (e^±1 ≈ 2.7×)
    off_share: float = 0.02  # effective-weight share below which a head is effectively switched off
    drift_per_tenth: float = 0.05  # Δ log σ over the last 10% of training still counted as drifting
    min_task_progress: float = 0.05  # relative fall of an unweighted loss counted as learning
    weighting_dominance: float = 3.0  # weighted total fell this many times more than the unweighted total


def _ident(dims: tuple[tuple[str, str], ...]) -> tuple[str, tuple[tuple[str, str], ...]]:
    d = dict(dims)
    term = d.pop("term", "")
    return term, tuple(sorted(d.items()))


def _label(term: str, rest: tuple[tuple[str, str], ...]) -> str:
    extra = ",".join(f"{k}={v}" for k, v in rest if k != "task")
    return f"{term}[{extra}]" if extra else term


@dataclass(frozen=True)
class _Head:
    term: str
    rest: tuple[tuple[str, str], ...]
    label: str
    first: float
    last: float
    delta: float
    drift: float


class TaskWeighting:
    name: ClassVar[str] = "task_weighting"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Learned task weights (uncertainty weighting)"
    description: ClassVar[str] = (
        "Multi-task losses weighted by learned uncertainty (Kendall et al. 2018: subloss·exp(-2·log σ) + log σ). "
        "Which heads the model down-weights or effectively switches off, whether it gives up on a head whose own "
        "loss is not improving, whether the weights have converged, and whether the total loss fell because the "
        "tasks improved or only because the weights moved."
    )
    category: ClassVar[str] = "loss"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Effective weight exp(-2·log σ) ignores constant per-task weights from the config.",
        "Uses training losses; a head can be down-weighted for good reasons (noisy labels).",
    )
    Params: ClassVar[type[AnalysisParams]] = TaskWeightingParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = (
            params
            if isinstance(params, TaskWeightingParams)
            else TaskWeightingParams.model_validate(params.model_dump())
        )
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        sig = {
            _ident(k.dims): s
            for k, s in ctx.target.series.items()
            if k.name == "task_log_sigma" and k.split in (Split.TRAIN, Split.UNKNOWN) and len(s) >= 3
        }
        if not sig:
            res.skipped_reason = (
                "no learned task weights (train/task_log_sigma[term=…], e.g. */uncertainty_weight_log_sigma)."
            )
            return res
        uloss = {}
        for k, s in ctx.target.series.items():
            if k.name.startswith("uloss/") and k.split in (Split.TRAIN, Split.UNKNOWN):
                uloss[(k.name.removeprefix("uloss/"), k.dims)] = s

        rows: list[_Head] = []
        for (term, rest), s in sig.items():
            first, last = _early(s, 0.1), _tail_mean(s, 0.1)
            _, v = s.finite()
            tail = v[-max(2, v.size // 10) :]
            drift = float(tail[-1] - tail[0]) if tail.size >= 2 else 0.0
            rows.append(_Head(term, rest, _label(term, rest), first, last, last - first, drift))
        weights = {r.label: math.exp(-2 * r.last) for r in rows}
        wtotal = sum(weights.values()) or 1.0
        table = fb.evidence(
            "Learned log σ per head (start → end, effective weight exp(-2·log σ) share at the end): "
            + "; ".join(
                f"{r.label} {r.first:.2f}→{r.last:.2f} ({weights[r.label] / wtotal:.0%})"
                for r in sorted(rows, key=lambda r: -abs(r.delta))[:8]
            )
            + (f"; … {len(rows) - 8} more heads" if len(rows) > 8 else ""),
            run=run.ref,
            metric="train/task_log_sigma[term=*]",
            data={r.label: {"first": r.first, "last": r.last, "share": weights[r.label] / wtotal} for r in rows},
        )
        res.plots.append(
            PlotSpec(
                id="task_weighting.share",
                title="Effective weight share per head at the end of training",
                kind="bar",
                x_label="head",
                y_label="share of exp(-2·log σ)",
                series=[{"name": "share", "points": [[k, v / wtotal] for k, v in sorted(weights.items())]}],
            )
        )

        down: list[tuple[_Head, float | None]] = []
        gave_up: list[tuple[_Head, float | None]] = []
        up: list[_Head] = []
        for r in rows:
            if r.delta >= p.sigma_move:
                ul = uloss.get((r.term, r.rest))
                prog = None
                if ul is not None:
                    e0, e1 = _early(ul, 0.2), _tail_mean(ul, 0.2)
                    prog = (e0 - e1) / abs(e0) if e0 else None
                (gave_up if prog is not None and prog < p.min_task_progress else down).append((r, prog))
            elif r.delta <= -p.sigma_move:
                up.append(r)
        if gave_up:
            evs = [table] + [
                fb.evidence(
                    f"{r.label}: log σ {r.first:.2f}→{r.last:.2f} (weight ×{math.exp(-2 * r.delta):.2f}) while its "
                    f"unweighted loss changed {-(prog or 0):+.1%}",
                    run=run.ref,
                    metric=f"train/uloss/{r.term}",
                    value=r.last,
                    baseline_value=r.first,
                )
                for r, prog in gave_up
            ]
            names = ", ".join(r.label for r, _ in gave_up)
            res.findings.append(
                fb.finding(
                    f"The model is giving up on {names}",
                    "These heads' learned uncertainty rose — their effective weight fell — while their own (unweighted) "
                    "loss did not improve: the optimizer lowers the total by turning the head down instead of learning it.",
                    severity=Severity.HIGH,
                    confidence=Confidence.MEDIUM,
                    evidence=evs,
                    hypotheses=[
                        fb.hypothesis(
                            "The head's targets are noisy or inconsistent (labels, assignment)", supporting=evs[1:2]
                        ),
                        fb.hypothesis(
                            "The head conflicts with the others on shared features",
                            supporting=evs[1:2],
                            falsifiable_by="Gradient-cosine probe (LossGradProbe) on the shared trunk",
                        ),
                        fb.hypothesis("log σ has no lower/upper bound or regularization", supporting=evs[1:2]),
                    ],
                    recommendations=[
                        fb.proposal(
                            f"Fix the weight of {gave_up[0][0].label} for one run",
                            hypothesis_ids=(),
                            controlled_variable="learned vs fixed task weight",
                            fixed_variables=("architecture", "data", "schedule"),
                            expected_outcome="The head's unweighted loss falls; its evaluation metrics improve",
                            measurements=(f"train/uloss/{gave_up[0][0].term}", "per-class evaluation"),
                            acceptance_criterion="Head metrics improve beyond seed noise without hurting the others",
                            estimated_cost_gpu_hours=run.gpu_hours,
                            information_gain=0.6,
                            priority=1,
                        )
                    ],
                    key="gave_up",
                )
            )
        if down:
            names = ", ".join(r.label for r, _ in down)
            res.findings.append(
                fb.finding(
                    f"The model down-weights {names}",
                    "Learned log σ rose by ≥ "
                    + f"{p.sigma_move:g} ("
                    + ", ".join(f"{r.label} weight ×{math.exp(-2 * r.delta):.2f}" for r, _ in down)
                    + "). Their own losses still improve, so this is re-balancing rather than abandonment — but these heads "
                    "now shape the shared features less.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[table],
                    key="down",
                )
            )
        off = [lbl for lbl, w in weights.items() if w / wtotal < p.off_share and len(weights) > 1]
        if off:
            res.findings.append(
                fb.finding(
                    f"{', '.join(off)} {'is' if len(off) == 1 else 'are'} effectively switched off",
                    f"Less than {p.off_share:.0%} of the effective loss weight goes to these heads at the end of training.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[table],
                    key="off",
                )
            )
        if up:
            res.findings.append(
                fb.finding(
                    f"The model increasingly trusts {', '.join(r.label for r in up)}",
                    "Learned log σ fell ("
                    + ", ".join(f"{r.label} weight ×{math.exp(-2 * r.delta):.1f}" for r in up)
                    + ").",
                    severity=Severity.INFO,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[table],
                    key="up",
                )
            )
        drifting = [r for r in rows if abs(r.drift) >= p.drift_per_tenth]
        if drifting:
            res.findings.append(
                fb.finding(
                    "Learned task weights have not converged",
                    "log σ still moves in the last 10% of training ("
                    + ", ".join(f"{r.label} {r.drift:+.2f}" for r in drifting)
                    + "): the loss balance at the end is not settled, so the final checkpoint depends on when training stopped.",
                    severity=Severity.LOW,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[table],
                    key="drift",
                )
            )

        tw, tu = ctx.target.find("loss", "train"), ctx.target.find("uloss", "train")
        if tw is not None and tu is not None:
            w0, w1, u0, u1 = _early(tw, 0.2), _tail_mean(tw, 0.2), _early(tu, 0.2), _tail_mean(tu, 0.2)
            dw = w0 - w1
            du = (u0 - u1) / abs(u0) if u0 else 0.0
            ev = fb.evidence(
                f"Weighted total {fmt(w0, 4)}→{fmt(w1, 4)}; unweighted total {fmt(u0, 4)}→{fmt(u1, 4)} ({-du:+.1%})",
                run=run.ref,
                metric="train/loss vs train/uloss",
            )
            wrel = dw / abs(w0) if w0 else 0.0
            if wrel > 0 and (du <= 0 or wrel > p.weighting_dominance * du):
                res.findings.append(
                    fb.finding(
                        "The total loss fell mostly because the task weights moved",
                        f"The weighted total fell {wrel:.0%} but the unweighted task losses only {du:+.0%}: most of the "
                        "decrease comes from log σ (weights and the +log σ terms), not from better predictions. Judge "
                        "progress on the unweighted losses and on evaluation.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.HIGH,
                        label=EpistemicLabel.COMPUTED,
                        evidence=[ev, table],
                        key="weights_drive_total",
                    )
                )
            if w1 < 0 <= w0:
                res.notes.append(
                    "The weighted total loss is negative at the end — expected with uncertainty weighting "
                    "(the +log σ terms go below zero); it is not a bug."
                )
        if not res.findings:
            res.notes.append("Learned task weights are stable and no head is down-weighted materially.")
        return res


TASK_WEIGHTING = TaskWeighting()

PLUGINS = (UNCERTAINTY_HEALTH, TEMPORAL_MEMORY, LOSS_INTERACTIONS, TASK_WEIGHTING)
