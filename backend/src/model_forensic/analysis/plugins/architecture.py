"""Architecture-level analyses over metrics logged by the instrumentation kit (model_forensic_instrument).

* module_health      per-module gradient / update health: dead, not learning, starved relative to others
* decoder_layers     per-decoder-layer losses: saturation, later layers getting worse, still improving
* modality_ablation  evaluation with a branch zeroed: each modality's contribution, overall and per slice
* feature_health     feature-map statistics: collapsed / dead features, scale imbalance, non-finite, explosion

Each analysis is skipped with an explanation when the run did not log the required metrics.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median
from typing import Any, ClassVar

import numpy as np

from model_forensic.analysis import signal
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
from model_forensic.domain.findings import Confidence, EpistemicLabel, Evidence, Finding, Severity
from model_forensic.domain.metrics import MetricSeries, Split

AblationTable = dict[str, dict[tuple[tuple[str, str], ...], dict[str, float]]]
KIT_HINT = "Log it with model_forensic_instrument (see instrument/README.md)."


def _by_dim(data: RunData, name: str, dim: str, split: Split | None = Split.TRAIN) -> dict[str, MetricSeries]:
    out = {}
    for k, s in data.series.items():
        if k.name == name and (split is None or k.split == split) and dim in k.dim_dict and len(k.dims) == 1:
            out[k.dim_dict[dim]] = s
    return out


def _late(s: MetricSeries, frac: float = 0.2) -> float:
    _, v = s.finite()
    return float(np.median(signal.tail(v, frac, min_points=1))) if v.size else math.nan


def _tail_mean(s: MetricSeries, frac: float = 0.2) -> float:
    _, v = s.finite()
    return float(np.mean(signal.tail(v, frac, min_points=1))) if v.size else math.nan


def _early(s: MetricSeries, frac: float = 0.2) -> float:
    _, v = s.finite()
    if not v.size:
        return math.nan
    k = max(1, round(v.size * frac))
    return float(np.median(v[:k]))


# --------------------------------------------------------------------------------------------- module health
class ModuleHealthParams(AnalysisParams):
    min_update_ratio: float = 1e-6
    starve_factor: float = 20.0
    unstable_update_ratio: float = 0.05
    stall_factor: float = 10.0


class ModuleHealth:
    name: ClassVar[str] = "module_health"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Module learning health"
    description: ClassVar[str] = (
        "Per-module gradient norm and optimizer update ratio (||Δθ||/||θ||): finds branches that get no gradient, "
        "barely move, learn far slower than the rest of the network, or stop learning early."
    )
    category: ClassVar[str] = "architecture"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Update ratios depend on optimizer and parameter scale; compare modules within a run, not absolute values across optimizers.",
    )
    Params: ClassVar[type[AnalysisParams]] = ModuleHealthParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, ModuleHealthParams) else ModuleHealthParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        upd = _by_dim(ctx.target, "update_ratio", "module")
        grad = _by_dim(ctx.target, "grad_norm", "module")
        dead = _by_dim(ctx.target, "grad_dead_frac", "module")
        if not upd and not grad:
            res.skipped_reason = f"no per-module train/update_ratio or train/grad_norm metrics. {KIT_HINT}"
            return res

        late_u = {m: _late(s) for m, s in upd.items()}
        early_u = {m: _early(s) for m, s in upd.items()}
        late_g = {m: _late(s) for m, s in grad.items()}
        late_d = {m: _late(s) for m, s in dead.items()}
        modules = sorted(set(late_u) | set(late_g))
        table = fb.evidence(
            "Per-module late-training medians: "
            + "; ".join(f"{m}: update {fmt(late_u.get(m), 3)}, grad {fmt(late_g.get(m), 3)}" for m in modules),
            run=run.ref,
            metric="train/update_ratio[module=*]",
            data={"update_ratio": late_u, "grad_norm": late_g},
        )
        res.plots.append(
            PlotSpec(
                id="module_health.update",
                title="Late-training update ratio per module",
                kind="bar",
                x_label="module",
                y_label="||Δθ||/||θ||",
                series=[{"name": "update_ratio", "points": [[m, late_u[m]] for m in sorted(late_u)]}],
            )
        )

        for m in modules:
            ms = f"module={m}"
            if late_d.get(m, 0.0) >= 0.99 or late_g.get(m, 1.0) == 0.0:
                ev = fb.evidence(
                    f"{m}: {late_d.get(m, 1.0):.0%} of parameter tensors have no gradient; grad norm {fmt(late_g.get(m), 3)}",
                    label=EpistemicLabel.OBSERVED,
                    run=run.ref,
                    metric=f"train/grad_dead_frac[{ms}]",
                    value=late_d.get(m),
                )
                res.findings.append(
                    fb.finding(
                        f"{m} receives no gradient",
                        f"The {m} branch gets no gradient late in training, so it does not learn at all.",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        label=EpistemicLabel.OBSERVED,
                        evidence=[ev, table],
                        hypotheses=[
                            fb.hypothesis(
                                f"{m} is frozen on purpose (check frozen stages / requires_grad)", supporting=[ev]
                            ),
                            fb.hypothesis(
                                f"{m}'s output is detached or unused in the forward pass (e.g. not wired into fusion)",
                                supporting=[ev],
                                falsifiable_by=f"Evaluate with {m} zeroed: no metric change confirms it is unused",
                            ),
                        ],
                        key=f"dead|{m}",
                    )
                )
                continue
            u = late_u.get(m)
            if u is None or not math.isfinite(u):
                continue
            others = [v for k, v in late_u.items() if k != m and math.isfinite(v) and v > 0]
            ref = median(others) if others else None
            if u < p.min_update_ratio:
                ev = fb.evidence(
                    f"{m}: late update ratio {fmt(u, 3)} (< {p.min_update_ratio:g}) while gradients are non-zero "
                    f"(grad norm {fmt(late_g.get(m), 3)})",
                    run=run.ref,
                    metric=f"train/update_ratio[{ms}]",
                    value=u,
                )
                res.findings.append(
                    fb.finding(
                        f"{m} barely changes during training",
                        f"{m} receives gradients but its weights effectively do not move.",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        evidence=[ev, table],
                        hypotheses=[
                            fb.hypothesis(
                                f"The learning rate for {m}'s parameter group is ~0 (lr multiplier, scheduler, frozen-by-LR)",
                                supporting=[ev],
                                falsifiable_by=f"Check the optimizer param groups / lr_mult for {m}",
                            )
                        ],
                        key=f"still|{m}",
                    )
                )
            elif ref and u < ref / p.starve_factor:
                ev = fb.evidence(
                    f"{m}: late update ratio {fmt(u, 3)} vs median {fmt(ref, 3)} of the other modules "
                    f"({ref / u:.0f}× lower)",
                    run=run.ref,
                    metric=f"train/update_ratio[{ms}]",
                    value=u,
                    baseline_value=ref,
                )
                res.findings.append(
                    fb.finding(
                        f"{m} learns {ref / u:.0f}× slower than the rest of the network",
                        f"{m}'s weights move far less per step than other modules', so it is under-trained relative to them.",
                        severity=Severity.HIGH,
                        confidence=Confidence.MEDIUM,
                        evidence=[ev, table],
                        hypotheses=[
                            fb.hypothesis(
                                f"A small learning-rate multiplier or large weight decay on {m}",
                                supporting=[ev],
                                falsifiable_by=f"Set {m}'s lr multiplier to 1.0; its update ratio should rise to the others' level",
                            ),
                            fb.hypothesis(
                                f"Fusion down-weights {m}'s features, so little gradient reaches it",
                                supporting=[ev],
                                falsifiable_by=f"Evaluate with {m} zeroed: a small metric drop supports this",
                            ),
                        ],
                        recommendations=[
                            fb.proposal(
                                f"Rebalance {m}'s learning rate",
                                hypothesis_ids=(),
                                controlled_variable=f"{m} learning-rate multiplier",
                                fixed_variables=("architecture", "data", "schedule"),
                                expected_outcome=f"{m} update ratio within ~3× of other modules; metrics that depend on {m} improve",
                                measurements=(
                                    f"train/update_ratio[{ms}]",
                                    "headline metrics",
                                    "metrics sliced where this branch matters",
                                ),
                                acceptance_criterion="Metric gain beyond seed noise without instability",
                                estimated_cost_gpu_hours=run.gpu_hours,
                                information_gain=0.7,
                                priority=1,
                            )
                        ],
                        key=f"starved|{m}",
                    )
                )
            elif u > p.unstable_update_ratio:
                ev = fb.evidence(
                    f"{m}: late update ratio {fmt(u, 3)} (> {p.unstable_update_ratio:g})",
                    run=run.ref,
                    metric=f"train/update_ratio[{ms}]",
                    value=u,
                )
                res.findings.append(
                    fb.finding(
                        f"{m} takes very large update steps",
                        f"{m}'s weights change by more than {p.unstable_update_ratio:.0%} per step.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        evidence=[ev],
                        key=f"large|{m}",
                    )
                )
            # stalled early relative to the others (LR decay affects all modules equally)
            e = early_u.get(m)
            if e and e > 0 and u > 0:
                decay = e / u
                other_decays = [early_u[k] / late_u[k] for k in late_u if k != m and early_u.get(k) and late_u.get(k)]
                if other_decays and decay > median(other_decays) * p.stall_factor:
                    ev = fb.evidence(
                        f"{m}: update ratio fell {decay:.0f}× from early to late training vs {median(other_decays):.1f}× "
                        "for the other modules",
                        run=run.ref,
                        metric=f"train/update_ratio[{ms}]",
                        value=decay,
                    )
                    res.findings.append(
                        fb.finding(
                            f"{m} stopped learning early",
                            f"{m}'s updates collapsed much faster than the rest of the network.",
                            severity=Severity.MEDIUM,
                            confidence=Confidence.MEDIUM,
                            evidence=[ev],
                            hypotheses=[
                                fb.hypothesis(
                                    f"{m}'s activations saturated or died (check feature statistics)", supporting=[ev]
                                )
                            ],
                            key=f"stall|{m}",
                        )
                    )
        if not res.findings:
            res.notes.append("All instrumented modules learn at comparable rates.")
        return res


# ------------------------------------------------------------------------------------------- decoder layers
class DecoderLayersParams(AnalysisParams):
    saturation_gain: float = 0.01
    still_improving_gain: float = 0.05
    degrade_threshold: float = 0.02


class DecoderLayers:
    name: ClassVar[str] = "decoder_layers"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Decoder depth"
    description: ClassVar[str] = (
        "Per-decoder-layer losses (loss/<term>[layer=k]): how much each refinement layer still improves, whether "
        "the last layers saturate or get worse, and whether a deeper decoder could help."
    )
    category: ClassVar[str] = "architecture"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Uses training losses; validation per-layer metrics are more reliable if logged.",
    )
    Params: ClassVar[type[AnalysisParams]] = DecoderLayersParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = (
            params
            if isinstance(params, DecoderLayersParams)
            else DecoderLayersParams.model_validate(params.model_dump())
        )
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        terms: dict[str, dict[int, MetricSeries]] = defaultdict(dict)
        # With learned (Kendall) task weighting each layer's weighted loss is scaled by its own sigma, so compare
        # the unweighted losses (uloss/…) when they are logged.
        prefix = (
            "uloss/"
            if any(k.name.startswith("uloss/") and "layer" in k.dim_dict for k in ctx.target.series)
            else "loss/"
        )
        tasks = {k.dim_dict.get("task") for k in ctx.target.series if k.name.startswith(prefix)}
        for k, s in ctx.target.series.items():
            d = k.dim_dict
            if (
                k.name.startswith(prefix)
                and "layer" in d
                and set(d) <= {"layer", "branch", "task"}
                and d["layer"].isdigit()
                and k.split in (Split.TRAIN, Split.UNKNOWN)
            ):
                # Branches (e.g. StreamPETR denoising queries, branch=dn) are analysed as their own decoder stack.
                name = "loss/" + k.name.removeprefix(prefix)
                if len(tasks) > 1 and d.get("task"):
                    name = f"{name}@{d['task']}"
                label = name if "branch" not in d else f"{name} ({d['branch']})"
                terms[label][int(d["layer"])] = s
        terms = {t: layers for t, layers in terms.items() if len(layers) >= 3}
        if not terms:
            res.skipped_reason = f"no per-decoder-layer losses (train/loss/<term>[layer=k], ≥3 layers). {KIT_HINT}"
            return res
        saturated: list[tuple[str, int, int, Evidence]] = []
        for term, layers in sorted(terms.items()):
            idx = sorted(layers)
            # Mean over the same final window for every layer: layers share steps, so the ratio is not
            # polluted by step-to-step loss noise (medians could pick different steps per layer).
            final = [_tail_mean(layers[i], 0.2) for i in idx]
            gains = [
                (final[i - 1] - final[i]) / abs(final[i - 1]) if final[i - 1] else 0.0 for i in range(1, len(final))
            ]
            res.plots.append(
                PlotSpec(
                    id=f"decoder_layers.{term}",
                    title=f"{term} by decoder layer (final)",
                    kind="line",
                    x_label="layer",
                    y_label=term,
                    series=[{"name": term, "points": [[i, v] for i, v in zip(idx, final, strict=True)]}],
                )
            )
            ev = fb.evidence(
                f"{term} by layer (late training): "
                + ", ".join(f"L{i}={fmt(v, 4)}" for i, v in zip(idx, final, strict=True))
                + "; gain per layer: "
                + ", ".join(f"L{idx[j + 1]} {g:+.1%}" for j, g in enumerate(gains)),
                run=run.ref,
                metric=f"train/{term}[layer=*]",
                data={"final": dict(zip(map(str, idx), final, strict=True))},
            )
            worse = [(idx[j + 1], g) for j, g in enumerate(gains) if g < -p.degrade_threshold]
            sat_tail = 0
            for g in reversed(gains):
                if 0 <= g < p.saturation_gain or abs(g) < p.saturation_gain:
                    sat_tail += 1
                else:
                    break
            if worse:
                res.findings.append(
                    fb.finding(
                        f"Later decoder layers make {term.removeprefix('loss/')} worse",
                        f"Layer(s) {', '.join(str(layer) for layer, _ in worse)} have higher {term} than the layer before.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        evidence=[ev],
                        hypotheses=[
                            fb.hypothesis(
                                "Over-refinement: later layers drift (query collapse, conflicting gradients)",
                                supporting=[ev],
                            ),
                            fb.hypothesis("Per-layer loss weights or matching differ between layers", supporting=[ev]),
                        ],
                        key=f"worse|{term}",
                    )
                )
            elif sat_tail >= 2:
                saturated.append((term, idx[len(idx) - sat_tail], sat_tail, ev))
            elif gains and gains[-1] > p.still_improving_gain:
                res.findings.append(
                    fb.finding(
                        f"Last decoder layer still improves {term.removeprefix('loss/')} by {gains[-1]:.0%}",
                        "Refinement has not converged at the last layer; a deeper decoder may help.",
                        severity=Severity.LOW,
                        confidence=Confidence.LOW,
                        evidence=[ev],
                        recommendations=[
                            fb.proposal(
                                f"Try {len(idx) + 2} decoder layers",
                                hypothesis_ids=(),
                                controlled_variable="number of decoder layers",
                                fixed_variables=("everything else",),
                                expected_outcome="Lower final-layer loss and better localization",
                                measurements=("headline metrics", "mATE"),
                                acceptance_criterion="Gain beyond seed noise",
                                estimated_cost_gpu_hours=run.gpu_hours,
                                information_gain=0.4,
                                priority=3,
                            )
                        ],
                        key=f"improving|{term}",
                    )
                )
        if saturated:
            # One finding for all loss terms that saturate: the decision (fewer layers) is the same.
            first_flat = max(f for _, f, _, _ in saturated)
            names = ", ".join(t.removeprefix("loss/") for t, _, _, _ in saturated)
            res.findings.append(
                fb.finding(
                    f"Decoder saturates after layer {first_flat - 1}",
                    f"For {names}, the last decoder layers each improve the loss by less than {p.saturation_gain:.0%}: "
                    f"layers {first_flat}+ add little.",
                    severity=Severity.LOW,
                    confidence=Confidence.MEDIUM,
                    evidence=[e for _, _, _, e in saturated],
                    recommendations=[
                        fb.proposal(
                            f"Try {first_flat} decoder layers",
                            hypothesis_ids=(),
                            controlled_variable="number of decoder layers",
                            fixed_variables=("everything else",),
                            expected_outcome="Same accuracy at lower cost, or capacity can be moved elsewhere",
                            measurements=("headline metrics", "training time"),
                            acceptance_criterion="Headline metric within seed noise",
                            estimated_cost_gpu_hours=run.gpu_hours,
                            information_gain=0.4,
                            priority=3,
                        )
                    ],
                    key="saturated",
                )
            )
        res.findings.extend(self._denoising(fb, run, terms))
        if not res.findings:
            res.notes.append("Decoder layers improve steadily; no saturation or degradation detected.")
        return res

    @staticmethod
    def _denoising(fb: FindingBuilder, run: Any, terms: dict[str, dict[int, MetricSeries]]) -> list[Finding]:
        """Denoising-query branch health: compare each dn stack with its matched (main) counterpart."""
        out: list[Finding] = []
        trivial: list[tuple[str, float, Evidence]] = []
        for label, layers in sorted(terms.items()):
            if not label.endswith(" (dn)"):
                continue
            main = terms.get(label.removesuffix(" (dn)"))
            last_dn = layers[max(layers)]
            dn_final, dn_early = _tail_mean(last_dn, 0.2), _early(last_dn, 0.2)
            name = label.removeprefix("loss/").removesuffix(" (dn)")
            if main:
                m_final = _tail_mean(main[max(main)], 0.2)
                if m_final > 0 and dn_final / m_final < 0.15:
                    ev = fb.evidence(
                        f"Last-layer {name}: denoising {fmt(dn_final, 4)} vs matched {fmt(m_final, 4)} "
                        f"({m_final / dn_final:.0f}× lower)",
                        run=run.ref,
                        metric=f"train/{label}",
                        value=dn_final,
                        baseline_value=m_final,
                    )
                    trivial.append((name, m_final / dn_final, ev))
            if dn_early > 0 and dn_final / dn_early > 0.9:
                ev = fb.evidence(
                    f"Last-layer denoising {name} loss: early {fmt(dn_early, 4)} → late {fmt(dn_final, 4)}",
                    run=run.ref,
                    metric=f"train/{label}",
                    value=dn_final,
                    baseline_value=dn_early,
                )
                out.append(
                    fb.finding(
                        f"Denoising branch does not learn ({name})",
                        "The denoising loss barely decreases over training.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        evidence=[ev],
                        hypotheses=[
                            fb.hypothesis(
                                "Denoising queries are masked incorrectly from matched queries (attention mask)",
                                supporting=[ev],
                            )
                        ],
                        key=f"dn_stall|{name}",
                    )
                )
        if trivial:
            names = ", ".join(n for n, _, _ in trivial)
            ratio = min(r for _, r, _ in trivial)
            evs = [e for _, _, e in trivial]
            out.insert(
                0,
                fb.finding(
                    f"Denoising task is trivial ({names})",
                    f"Denoising queries reach losses at least {ratio:.0f}× lower than matched queries: the noise added to "
                    "ground-truth boxes may be too small to teach refinement.",
                    severity=Severity.LOW,
                    confidence=Confidence.MEDIUM,
                    evidence=evs,
                    hypotheses=[
                        fb.hypothesis(
                            "Box/label noise scale of the denoising groups is too small",
                            supporting=evs,
                            falsifiable_by="Raise the dn box noise scale; dn loss should rise and matched loss fall",
                        )
                    ],
                    key="dn_trivial",
                ),
            )
        return out


# ---------------------------------------------------------------------------------------- modality ablation
class ModalityAblationParams(AnalysisParams):
    negligible_rel: float = 0.01
    headline_metrics: list[str] = ["NDS", "mAP", "AMOTA"]


class ModalityAblation:
    name: ClassVar[str] = "modality_ablation"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Modality contribution (eval-time ablation)"
    description: ClassVar[str] = (
        "Compares evaluation with each branch zeroed (metric[ablation=no_X]) to the normal evaluation: what each modality "
        "contributes to the trained model, overall and per slice (range, class)."
    )
    category: ClassVar[str] = "architecture"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Zeroing a branch at evaluation is not the same as training without it; read contributions as relative.",
    )
    Params: ClassVar[type[AnalysisParams]] = ModalityAblationParams

    @staticmethod
    def _table(data: RunData) -> dict[str, dict[tuple[tuple[str, str], ...], dict[str, float]]]:
        """metric name → other-dims → ablation → final value."""
        t: dict[str, dict[tuple[tuple[str, str], ...], dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        for k, s in data.series.items():
            if k.split not in (Split.VAL, Split.TEST):
                continue
            d = k.dim_dict
            if "ablation" not in d:
                continue
            rest = tuple(sorted((a, b) for a, b in d.items() if a != "ablation"))
            v = s.aggregate()
            if math.isfinite(v):
                t[k.name][rest][d["ablation"]] = v
        return t

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = (
            params
            if isinstance(params, ModalityAblationParams)
            else ModalityAblationParams.model_validate(params.model_dump())
        )
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref, *([ctx.baseline.run.ref] if ctx.baseline else [])])
        table = self._table(ctx.target)
        if not any("none" in v for dims in table.values() for v in dims.values()):
            res.skipped_reason = f"no evaluation-time ablations (val/<metric>[ablation=none|no_<branch>]). {KIT_HINT}"
            return res
        ablations = sorted({a for dims in table.values() for v in dims.values() for a in v if a != "none"})
        base_table = self._table(ctx.baseline) if ctx.baseline else {}

        def contribution(
            name: str, dims: tuple[tuple[str, str], ...], ab: str, tbl: AblationTable = table
        ) -> float | None:
            vals = tbl.get(name, {}).get(dims, {})
            if "none" not in vals or ab not in vals or vals["none"] == 0:
                return None
            hib = ctx.registry.lookup(name).higher_is_better
            drop = (vals["none"] - vals[ab]) if hib is not False else (vals[ab] - vals["none"])
            return float(drop / abs(vals["none"]))

        bars = []
        for ab in ablations:
            branch = ab.removeprefix("no_")
            heads = [(m, c) for m in p.headline_metrics if (c := contribution(m, (), ab)) is not None]
            if not heads:
                continue
            bars.append([branch, heads[0][1]])
            evs: list[Evidence] = [
                fb.evidence(
                    f"Zeroing {branch}: {m} {table[m][()]['none']:.4g} → {table[m][()][ab]:.4g} (drop {c:.1%})",
                    run=run.ref,
                    metric=f"val/{m}[ablation={ab}]",
                    value=table[m][()][ab],
                    baseline_value=table[m][()]["none"],
                )
                for m, c in heads
            ]
            # per-slice contributions (e.g. range) for any metric that has them
            slices: dict[str, list[tuple[str, float]]] = defaultdict(list)
            for name, by_dims in table.items():
                for dims in by_dims:
                    if len(dims) == 1 and (c := contribution(name, dims, ab)) is not None:
                        slices[f"{name} by {dims[0][0]}"].append((dims[0][1], c))
            for label, vals in sorted(slices.items()):
                evs.append(
                    fb.evidence(
                        f"Drop in {label} when {branch} is zeroed: "
                        + ", ".join(f"{v} {c:.1%}" for v, c in sorted(vals)),
                        run=run.ref,
                        data={"contribution": dict(vals)},
                    )
                )
            main = heads[0][1]
            if main < -p.negligible_rel:
                res.findings.append(
                    fb.finding(
                        f"The model is better without {branch}",
                        f"Zeroing {branch} improves {heads[0][0]} by {-main:.1%}: the branch adds noise.",
                        severity=Severity.HIGH,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=evs,
                        hypotheses=[
                            fb.hypothesis(
                                f"{branch} features are mis-aligned or poorly normalized before fusion",
                                supporting=evs[:1],
                            )
                        ],
                        key=f"neg|{ab}",
                    )
                )
            elif abs(main) < p.negligible_rel:
                best_slice = max((c for vals in slices.values() for _, c in vals), default=0.0)
                res.findings.append(
                    fb.finding(
                        f"{branch} contributes almost nothing ({main:.1%} of {heads[0][0]})",
                        f"Zeroing {branch} at evaluation lowers {heads[0][0]} by only {main:.1%}"
                        + (f" (at most {best_slice:.1%} in any slice)" if slices else "")
                        + ": the trained model barely uses it.",
                        severity=Severity.HIGH,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=evs,
                        hypotheses=[
                            fb.hypothesis(
                                f"{branch} is under-trained (check its update ratio in module health)",
                                supporting=evs[:1],
                            ),
                            fb.hypothesis(
                                f"Fusion learned to ignore {branch} (feature scale or alignment)", supporting=evs[:1]
                            ),
                        ],
                        key=f"nil|{ab}",
                    )
                )
            else:
                text = f"Zeroing {branch} costs {main:.1%} {heads[0][0]}"
                strongest = max(
                    ((lab, v, c) for lab, vals in slices.items() for v, c in vals), key=lambda t: t[2], default=None
                )
                if strongest:
                    text += f"; it matters most for {strongest[0].split(' by ')[0]} at {strongest[1]} ({strongest[2]:.1%} drop)"
                res.findings.append(
                    fb.finding(
                        f"{branch} contribution: {main:.1%} of {heads[0][0]}",
                        text + ".",
                        severity=Severity.INFO,
                        confidence=Confidence.MEDIUM,
                        label=EpistemicLabel.COMPUTED,
                        evidence=evs,
                        key=f"contrib|{ab}",
                    )
                )
            if base_table:
                bc = contribution(heads[0][0], (), ab, base_table)
                if bc is not None:
                    ev = fb.evidence(
                        f"{branch} contribution to {heads[0][0]}: baseline {bc:+.1%} → candidate {main:+.1%}",
                        run=run.ref,
                        value=main,
                        baseline_value=bc,
                    )
                    if abs(main - bc) > p.negligible_rel:
                        res.findings.append(
                            fb.finding(
                                f"{branch} contribution {'grew' if main > bc else 'shrank'} vs baseline",
                                f"The candidate relies {'more' if main > bc else 'less'} on {branch} than the baseline.",
                                severity=Severity.MEDIUM if main < bc else Severity.INFO,
                                confidence=Confidence.MEDIUM,
                                label=EpistemicLabel.COMPUTED,
                                evidence=[ev],
                                key=f"delta|{ab}",
                            )
                        )
        if bars:
            res.plots.append(
                PlotSpec(
                    id="modality_ablation.contrib",
                    title="Contribution per branch (drop when zeroed)",
                    kind="bar",
                    x_label="branch",
                    y_label="relative contribution",
                    series=[{"name": "contribution", "points": bars}],
                )
            )
        return res


# ------------------------------------------------------------------------------------------- feature health
class FeatureHealthParams(AnalysisParams):
    dead_zero_frac: float = 0.95
    collapse_rel_std: float = 1e-3
    scale_imbalance: float = 30.0
    growth: float = 20.0


class FeatureHealth:
    name: ClassVar[str] = "feature_health"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Feature-map health"
    description: ClassVar[str] = (
        "Statistics of instrumented feature maps (per module and per scale of multi-scale outputs): dead or collapsed "
        "maps, scales whose magnitudes differ by orders of magnitude, non-finite values, exploding activations."
    )
    category: ClassVar[str] = "architecture"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Statistics are over whole tensors; channel-level collapse is not visible.",
    )
    Params: ClassVar[type[AnalysisParams]] = FeatureHealthParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = (
            params
            if isinstance(params, FeatureHealthParams)
            else FeatureHealthParams.model_validate(params.model_dump())
        )
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        stats: dict[str, dict[tuple[str, str], MetricSeries]] = defaultdict(dict)  # stat → (module, scale) → series
        for k, s in ctx.target.series.items():
            d = k.dim_dict
            if k.name.startswith("feat_") and "module" in d:
                stats[k.name][(d["module"], d.get("scale", ""))] = s
        if not stats:
            res.skipped_reason = f"no feature statistics (train/feat_rms[module=…,scale=…]). {KIT_HINT}"
            return res

        def label(mod: str, scale: str) -> str:
            return f"{mod}" + (f" scale {scale}" if scale else "")

        rms = {k: _late(s) for k, s in stats.get("feat_rms", {}).items()}
        std = {k: _late(s) for k, s in stats.get("feat_std", {}).items()}
        zero = {k: _late(s) for k, s in stats.get("feat_zero_frac", {}).items()}
        nonf = {k: max(s.finite()[1].max(initial=0.0), 0.0) for k, s in stats.get("feat_nonfinite_frac", {}).items()}
        for key, v in sorted(nonf.items()):
            if v > 0:
                ev = fb.evidence(
                    f"{label(*key)}: up to {v:.2%} non-finite values",
                    label=EpistemicLabel.OBSERVED,
                    run=run.ref,
                    metric=f"train/feat_nonfinite_frac[module={key[0]}]",
                    value=v,
                )
                res.findings.append(
                    fb.finding(
                        f"Non-finite activations in {label(*key)}",
                        "NaN/Inf values appear in this feature map.",
                        severity=Severity.CRITICAL,
                        confidence=Confidence.HIGH,
                        label=EpistemicLabel.OBSERVED,
                        evidence=[ev],
                        key=f"nonfinite|{key}",
                    )
                )
        mods = defaultdict(list)
        for key in set(rms) | set(zero):
            mods[key[0]].append(key)
        collapsed_keys: set[tuple[str, str]] = set()
        for mod, keys in sorted(mods.items()):
            med_std = median([std[k] for k in keys if k in std and std[k] > 0] or [1.0])
            for key in sorted(keys):
                z, sd = zero.get(key, 0.0), std.get(key)
                collapsed = z >= p.dead_zero_frac or (
                    sd is not None and med_std > 0 and sd < p.collapse_rel_std * med_std
                )
                if collapsed:
                    collapsed_keys.add(key)
                    ev = fb.evidence(
                        f"{label(*key)}: zero fraction {z:.0%}, std {fmt(sd, 3)} (module median std {fmt(med_std, 3)})",
                        run=run.ref,
                        metric=f"train/feat_zero_frac[module={key[0]}" + (f",scale={key[1]}]" if key[1] else "]"),
                        value=z,
                    )
                    res.findings.append(
                        fb.finding(
                            f"{label(*key)} has collapsed",
                            "This feature map is almost entirely zero / constant late in training, so it carries no information.",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            evidence=[ev],
                            hypotheses=[
                                fb.hypothesis(
                                    "Dead ReLUs after a large update or a too-high learning rate on this branch",
                                    supporting=[ev],
                                ),
                                fb.hypothesis(
                                    "Input to this scale is empty (e.g. stride too large for sparse radar points)",
                                    supporting=[ev],
                                    falsifiable_by="Check the input occupancy at this stride",
                                ),
                            ],
                            key=f"collapsed|{key}",
                        )
                    )
            # Collapsed scales are reported above; imbalance is about the live scales only.
            scaled = [(k, rms[k]) for k in keys if k in rms and k[1] != "" and rms[k] > 0 and k not in collapsed_keys]
            if len(scaled) >= 2:
                hi, lo = max(scaled, key=lambda t: t[1]), min(scaled, key=lambda t: t[1])
                if hi[1] / lo[1] > p.scale_imbalance:
                    ev = fb.evidence(
                        f"{mod} RMS by scale: " + ", ".join(f"{k[1]}={fmt(v, 3)}" for k, v in sorted(scaled)),
                        run=run.ref,
                        metric=f"train/feat_rms[module={mod},scale=*]",
                        data={"rms": {k[1]: v for k, v in scaled}},
                    )
                    res.findings.append(
                        fb.finding(
                            f"{mod} scales differ {hi[1] / lo[1]:.0f}× in magnitude",
                            f"Scale {hi[0][1]} is {hi[1] / lo[1]:.0f}× stronger than scale {lo[0][1]}; fusion may effectively ignore the weak scales.",
                            severity=Severity.MEDIUM,
                            confidence=Confidence.MEDIUM,
                            evidence=[ev],
                            hypotheses=[
                                fb.hypothesis(
                                    "Missing per-scale normalization before fusion",
                                    supporting=[ev],
                                    falsifiable_by="Add LayerNorm/BN per scale and compare the weak scale's contribution",
                                )
                            ],
                            key=f"imbalance|{mod}",
                        )
                    )
        for key, s in sorted(stats.get("feat_rms", {}).items()):
            e, late = _early(s), _late(s)
            if e > 0 and late / e > p.growth:
                ev = fb.evidence(
                    f"{label(*key)}: RMS grew {late / e:.0f}× ({fmt(e, 3)} → {fmt(late, 3)})",
                    run=run.ref,
                    metric=f"train/feat_rms[module={key[0]}]",
                    value=late,
                    baseline_value=e,
                )
                res.findings.append(
                    fb.finding(
                        f"Activations in {label(*key)} keep growing",
                        "Feature magnitude grows steadily — a precursor of instability.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        evidence=[ev],
                        key=f"growth|{key}",
                    )
                )
        if not res.findings:
            res.notes.append("Instrumented feature maps look healthy.")
        return res


MODULE_HEALTH = ModuleHealth()
DECODER_LAYERS = DecoderLayers()
MODALITY_ABLATION = ModalityAblation()
FEATURE_HEALTH = FeatureHealth()
PLUGINS = (MODULE_HEALTH, DECODER_LAYERS, MODALITY_ABLATION, FEATURE_HEALTH)
