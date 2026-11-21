"""Loss decomposition: dominant, dead, exploding and stalled terms; loss/eval contradictions."""

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
from model_forensic.domain.findings import Confidence, Evidence, Severity
from model_forensic.domain.metrics import MetricKey, MetricSeries, Split


class Params(AnalysisParams):
    component_prefix: str = "loss/"
    dominant_share: float = 0.7
    dead_abs_threshold: float = 1e-6
    stall_tail_frac: float = 0.5
    stall_min_rel_improvement: float = 0.01
    explode_slope: float = 0.5
    eval_metrics: list[str] = ["NDS", "mAP", "AMOTA"]


class LossForensics:
    name: ClassVar[str] = "loss_forensics"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Loss forensics"
    description: ClassVar[str] = (
        "Decomposes the total loss into logged components (loss/<term>[dims]) and detects dominant, "
        "dead, exploding and stalled terms, plus terms that improve while evaluation worsens."
    )
    category: ClassVar[str] = "loss"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Shares are computed on logged (weighted) values; unweighted magnitudes are not visible.",
    )
    Params: ClassVar[type[AnalysisParams]] = Params

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, Params) else Params.model_validate(params.model_dump())
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        res = AnalysisResult(analysis=self.name)
        comps: dict[MetricKey, MetricSeries] = {
            k: s
            for k, s in ctx.target.series.items()
            if k.name.startswith(p.component_prefix)
            and not k.dims
            and k.split in (Split.TRAIN, Split.UNKNOWN)
            and len(s) >= 6
        }
        if not comps:
            res.skipped_reason = f"no component loss series with prefix '{p.component_prefix}'"
            return res

        finals = {k: float(np.mean(signal.tail(s.finite()[1], 0.1))) for k, s in comps.items()}
        total = sum(abs(v) for v in finals.values()) or 1e-12
        shares = {k: abs(v) / total for k, v in finals.items()}
        res.plots.append(
            PlotSpec(
                id="loss_forensics.shares",
                title="Final loss share by component",
                kind="bar",
                x_label="component",
                y_label="share",
                series=[
                    {
                        "name": "share",
                        "points": [[str(k), round(v, 4)] for k, v in sorted(shares.items(), key=lambda kv: -kv[1])],
                    }
                ],
            )
        )
        share_ev = fb.evidence(
            "Final loss shares: "
            + ", ".join(
                f"{k.name.removeprefix(p.component_prefix)}={v:.0%}"
                for k, v in sorted(shares.items(), key=lambda kv: -kv[1])
            ),
            run=run.ref,
            data={str(k): v for k, v in shares.items()},
        )

        top_key, top_share = max(shares.items(), key=lambda kv: kv[1])
        if top_share >= p.dominant_share and len(comps) > 1:
            res.findings.append(
                fb.finding(
                    "Dominant loss term",
                    f"{top_key.name} accounts for {top_share:.0%} of the final total; other terms receive little gradient signal.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    evidence=[share_ev],
                    hypotheses=[
                        fb.hypothesis(
                            f"Loss weight on {top_key.name} is too high relative to the other terms",
                            supporting=[share_ev],
                            falsifiable_by=f"Halve the weight of {top_key.name}; check whether other terms and eval improve",
                        )
                    ],
                    key=str(top_key),
                )
            )

        growing: list[tuple[str, float]] = []
        decoupled: dict[str, list[tuple[str, float]]] = {}  # eval metric -> [(term, loss slope)]
        eval_info: dict[str, tuple[float, float, float]] = {}  # eval metric -> (late slope, final, best)
        eval_series = [
            s for name in p.eval_metrics if (s := ctx.target.find(name, Split.VAL.value)) is not None and len(s) >= 4
        ]

        for k, s in sorted(comps.items(), key=lambda kv: str(kv[0])):
            st, v = s.finite()
            if v.size < 6:
                continue
            ks = str(k)
            if float(np.max(np.abs(signal.tail(v, 0.5)))) < p.dead_abs_threshold:
                ev = fb.evidence(
                    f"{ks} stays below {p.dead_abs_threshold:g} in the second half of training",
                    run=run.ref,
                    metric=ks,
                    value=float(v[-1]),
                )
                res.findings.append(
                    fb.finding(
                        f"Dead loss term: {k.name}",
                        f"{ks} contributes no gradient for most of training.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.HIGH,
                        evidence=[ev],
                        hypotheses=[
                            fb.hypothesis(
                                "Term is disabled by a zero weight or masked out by the assigner/matcher",
                                supporting=[ev],
                            )
                        ],
                        key=ks,
                    )
                )
                continue
            slope = signal.normalized_slope(st[st.size // 2 :], v[v.size // 2 :])
            if slope > p.explode_slope:
                growing.append((ks, slope))
                continue
            imp = signal.relative_improvement(v, p.stall_tail_frac)
            first_half_imp = signal.relative_improvement(v[: v.size // 2 + 1], 1.0) if v.size >= 12 else float("nan")
            if (
                np.isfinite(imp)
                and abs(imp) < p.stall_min_rel_improvement
                and np.isfinite(first_half_imp)
                and first_half_imp > 0.05
            ):
                ev = fb.evidence(
                    f"{ks} improved {first_half_imp:.1%} in the first half but only {imp:+.2%} over the last {p.stall_tail_frac:.0%}",
                    run=run.ref,
                    metric=ks,
                    value=imp,
                )
                res.findings.append(
                    fb.finding(
                        f"Loss term stopped learning: {k.name}",
                        f"{ks} stalled while training continued.",
                        severity=Severity.LOW,
                        confidence=Confidence.MEDIUM,
                        evidence=[ev],
                        key=ks,
                    )
                )

            # Contradiction: term improves while an eval metric worsens over the same span.
            for es in eval_series:
                ess, esv = es.finite()
                if ess.size < 4:
                    continue
                lo = max(float(ess[0]), float(st[0]))
                mask_e, mask_l = ess >= lo, st >= lo
                if int(mask_e.sum()) < 3 or int(mask_l.sum()) < 3:
                    continue
                desc = ctx.registry.lookup(es.key.name)
                loss_trend = signal.normalized_slope(st[mask_l], v[mask_l])
                eval_trend = signal.normalized_slope(ess[mask_e], esv[mask_e])
                eval_worse = (eval_trend < -0.02) if desc.higher_is_better else (eval_trend > 0.02)
                late_mask = ess >= float(np.median(ess))
                late_eval = (
                    signal.normalized_slope(ess[late_mask], esv[late_mask]) if int(late_mask.sum()) >= 3 else 0.0
                )
                late_worse = (late_eval < -0.02) if desc.higher_is_better else (late_eval > 0.02)
                if loss_trend < -0.05 and (eval_worse or late_worse):
                    best = float(esv.max() if desc.higher_is_better else esv.min())
                    decoupled.setdefault(str(es.key), []).append((ks, loss_trend))
                    eval_info[str(es.key)] = (late_eval, float(esv[-1]), best)

        if growing:
            evs = [
                fb.evidence(f"{k} normalized slope over second half: {sl:+.2f}", run=run.ref, metric=k, value=sl)
                for k, sl in growing
            ]
            all_terms = len(growing) == len(comps)
            res.findings.append(
                fb.finding(
                    "All loss terms grow together"
                    if all_terms and len(growing) > 1
                    else f"Growing loss term(s): {', '.join(k for k, _ in growing)}",
                    (
                        "Every loss component increases in the second half of training; this points to a global optimization "
                        "instability rather than a single term."
                    )
                    if all_terms and len(growing) > 1
                    else "These loss components increase during the second half of training.",
                    severity=Severity.HIGH,
                    confidence=Confidence.MEDIUM,
                    evidence=evs,
                    hypotheses=[
                        fb.hypothesis(
                            "Global optimizer instability (learning rate / warmup / precision)", supporting=evs
                        )
                    ]
                    if all_terms
                    else [
                        fb.hypothesis("Loss weight or target scale of the growing term(s) is unstable", supporting=evs)
                    ],
                    key="growing",
                )
            )
        if decoupled:
            dec_evs: list[Evidence] = []
            for metric in sorted(decoupled):
                late, final, best = eval_info[metric]
                dec_evs.append(
                    fb.evidence(
                        f"{metric} late-training slope {late:+.2f}; final {fmt(final)} vs best {fmt(best)}",
                        run=run.ref,
                        metric=metric,
                        value=final,
                        baseline_value=best,
                    )
                )
            term_slopes = {t: sl for ts in decoupled.values() for t, sl in ts}
            dec_evs += [
                fb.evidence(f"{t} slope {sl:+.2f} (improving)", run=run.ref, metric=t, value=sl)
                for t, sl in sorted(term_slopes.items())
            ]
            metrics_txt = ", ".join(m.split("/")[-1] for m in sorted(decoupled))
            res.findings.append(
                fb.finding(
                    f"Training loss improves while {metrics_txt} worsen",
                    f"Loss terms {', '.join(sorted(term_slopes))} keep improving while evaluation ({metrics_txt}) degrades late in "
                    "training: the objective and the evaluation metric have decoupled.",
                    severity=Severity.HIGH,
                    confidence=Confidence.MEDIUM,
                    evidence=dec_evs,
                    hypotheses=[
                        fb.hypothesis(
                            "Overfitting to the training distribution",
                            supporting=dec_evs,
                            falsifiable_by="Evaluate earlier checkpoints; check train/val loss gap",
                        ),
                        fb.hypothesis(
                            "A loss term is misaligned with the evaluation criterion (e.g. matching cost vs. eval threshold)",
                            supporting=dec_evs,
                        ),
                    ],
                    key="decoupled",
                )
            )
        return res


PLUGIN = LossForensics()
