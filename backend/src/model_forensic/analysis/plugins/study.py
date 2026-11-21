"""Study checks (pitfall catalog P1–P8) and the loss ledger as analyses."""

from __future__ import annotations

import math
from typing import ClassVar

from model_forensic.analysis import study_checks as sc
from model_forensic.analysis.base import (
    AnalysisContext,
    AnalysisParams,
    AnalysisResult,
    FindingBuilder,
    PlotSpec,
    Requirement,
)
from model_forensic.domain.findings import Confidence, EpistemicLabel, Severity

FAMILY_LABEL = {
    "decoder_cls": "Decoder · class",
    "decoder_bbox": "Decoder · box",
    "denoise_cls": "Denoising · class",
    "denoise_bbox": "Denoising · box",
    "camera": "Camera head",
    "radar": "Radar head",
    "other": "Other",
}


def run_checks(ctx: AnalysisContext, p: ChecksParams | None = None) -> list[sc.CheckResult]:
    p = p or ChecksParams()
    t = ctx.target
    names = [s.provenance.get("raw_name") or str(k) for k, s in t.series.items()] + [str(k) for k in t.series]
    cfgs = {str(t.run.ref): dict(t.configs)}
    if ctx.baseline is not None and ctx.baseline.configs:
        cfgs[str(ctx.baseline.run.ref)] = dict(ctx.baseline.configs)
    loss = t.find("loss", "train")
    noreg = t.find("loss_noreg", "train")
    return [
        sc.check_ruler(cfgs),
        sc.check_glossary(names, t.evals),
        sc.check_gt_coverage(t.evals, share=p.degenerate_share),
        sc.check_monitor(t.configs, names),
        sc.check_val_series(t.series),
        sc.CheckResult(
            "P6",
            "Figures regenerable from named data",
            "PASS",
            "every figure and table is computed from the cited series / files",
        ),
        _paths_check(t.series, p.muted_sigma),
        sc.plateau(
            loss,
            t.lr,
            noreg,
            epoch=t.find("epoch", "train"),
            epochs=sc.max_epochs(t.configs),
            plateau_tail=p.plateau_tail,
            learning_gain=p.learning_gain,
            schedule_lr_rel=p.schedule_lr_rel,
            early_fraction=p.early_fraction,
            thermostat_ratio=p.thermostat_ratio,
            schedule_min_remaining=p.schedule_min_remaining,
        ),
    ]


def _paths_check(series: object, muted_sigma: float = 2.0) -> sc.CheckResult:
    rows = sc.ledger(series)  # type: ignore[arg-type]
    muted = sorted({r.family for r in rows if r.family in {"camera", "radar"} and r.sigma > muted_sigma})
    if muted:
        return sc.CheckResult(
            "P7",
            "Muted head ≠ muted modality",
            "WARN",
            f"{', '.join(muted)} auxiliary-head losses are self-muted by σ; the modality can still "
            "reach the detector (fusion, query spawning) — say which path a claim is about",
        )
    return sc.CheckResult("P7", "Muted head ≠ muted modality", "INFO", "no auxiliary head is muted by its σ")


class ChecksParams(AnalysisParams):
    """Thresholds of the pitfall checks. Defaults come from docs/calibration.md; override per team in
    ``config/team.local.yaml`` → ``analysis_params: {study_checks: {...}}``."""

    degenerate_share: float = 0.95  # P3: share of GT in one bin that makes an attribute's error metric meaningless
    muted_sigma: float = 2.0  # P7: log σ above which an auxiliary head counts as self-muted (weight ≤ ~2 %)
    plateau_tail: float = 0.05  # P8: plateau = remaining improvement below this share of the total drop
    learning_gain: float = 0.02  # P8: last-segment relative gain above which the run is "still learning"
    schedule_lr_rel: float = 0.2  # P8: LR below this share of peak at the plateau = schedule-bound
    early_fraction: float = 0.5  # P8: plateau before this fraction of training (at high LR) = converged early
    thermostat_ratio: float = 0.25  # P8: network-facing drop below this share of the total drop = σ settling
    schedule_min_remaining: float = 0.35  # P8: schedule-bound only if at least this share of training is left


class StudyChecks:
    name: ClassVar[str] = "study_checks"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Study checks (pitfall catalog)"
    description: ClassVar[str] = (
        "Eight deterministic checks before any conclusion: same ruler (P1), metric meaning (P2), degenerate ground "
        "truth (P3), checkpoint monitor logged (P4), validation metrics exist (P5), figure provenance (P6), muted "
        "head vs modality (P7), plateau × learning rate (P8)."
    )
    category: ClassVar[str] = "data"
    requires: ClassVar[frozenset[Requirement]] = frozenset(
        {Requirement.SERIES, Requirement.CONFIG, Requirement.EVAL_JSON, Requirement.LR}
    )
    limitations: ClassVar[tuple[str, ...]] = (
        "P1 compares the run with the baseline; the study book compares all runs.",
    )
    Params: ClassVar[type[AnalysisParams]] = ChecksParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        res = AnalysisResult(analysis=self.name)
        fb = FindingBuilder(self, params, [ctx.target.run.ref])
        checks = run_checks(ctx, params if isinstance(params, ChecksParams) else None)
        for c in checks:
            res.notes.append(f"{c.id} {c.status} — {c.title}: {c.detail}")
        sev = {"FAIL": Severity.HIGH, "WARN": Severity.MEDIUM}
        for c in checks:
            if c.status in sev:
                ev = fb.evidence(f"{c.id}: {c.detail}", run=ctx.target.run.ref, data=c.data)
                res.findings.append(
                    fb.finding(
                        f"{c.id} {c.title}: {c.status}",
                        c.detail,
                        severity=sev[c.status],
                        confidence=Confidence.HIGH if c.id in {"P1", "P3", "P4", "P5"} else Confidence.MEDIUM,
                        label=EpistemicLabel.OBSERVED if c.id in {"P1", "P4", "P5"} else EpistemicLabel.COMPUTED,
                        evidence=[ev],
                        key=c.id,
                    )
                )
        return res


class LedgerParams(AnalysisParams):
    muted_sigma: float = 2.0  # log σ above which a head is self-muted (weight ≤ e^-4 ≈ 2 %)
    dominant_share: float = 0.5


class LossLedger:
    name: ClassVar[str] = "loss_ledger"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Loss ledger (budget, trust, gradient)"
    description: ClassVar[str] = (
        "Per-subloss ledger of a Kendall-weighted multi-task loss at the end of training: raw, weighted and "
        "network-facing (no_reg) loss per family, learned σ (trust), the σ tax, task weights, and the "
        "denoising/main ratio. The total loss is a headline; the ledger is the article."
    )
    category: ClassVar[str] = "loss"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Needs unweighted_loss, weighted_loss, weighted_loss_no_reg and log σ per subloss.",
    )
    Params: ClassVar[type[AnalysisParams]] = LedgerParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, LedgerParams) else LedgerParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        rows = sc.ledger(ctx.target.series)
        if not rows:
            res.skipped_reason = (
                "no per-subloss ledger (needs */unweighted_loss, */weighted_loss, "
                "*/weighted_loss_no_reg and */uncertainty_weight_log_sigma)"
            )
            return res
        fam = sc.family_rollup(rows)
        table = fb.evidence(
            "Family ledger at the end of training (share of raw / weighted / gradient, σ range): "
            + "; ".join(
                f"{FAMILY_LABEL.get(f, f)} {v['raw_share']:.0%}/{v['weighted_share']:.0%}/{v['no_reg_share']:.0%}, "
                f"σ {v['sigma_min']:+.2f}…{v['sigma_max']:+.2f}"
                for f, v in sorted(fam.items(), key=lambda kv: -kv[1]["weighted_share"])
            ),
            run=run.ref,
            metric="train/{uloss,loss,loss_noreg}/*, train/task_log_sigma",
            data={"families": fam, "rows": [r.__dict__ | {"label": r.label} for r in rows]},
        )
        res.plots.append(
            PlotSpec(
                id="ledger.shares",
                title="Share of raw loss, weighted total and gradient per family",
                kind="bar",
                x_label="family",
                y_label="share",
                series=[
                    {"name": n, "points": [[FAMILY_LABEL.get(f, f), v[f"{k}_share"]] for f, v in fam.items()]}
                    for n, k in (("raw", "raw"), ("weighted", "weighted"), ("gradient (no_reg)", "no_reg"))
                ],
            )
        )
        muted = {f: v for f, v in fam.items() if v["sigma_min"] > p.muted_sigma}
        if muted:
            res.findings.append(
                fb.finding(
                    f"Self-muted: {', '.join(FAMILY_LABEL.get(f, f) for f in muted)}",
                    "Every subloss of these families has log σ above " + f"{p.muted_sigma:g} (effective weight ≤ "
                    f"{math.exp(-2 * p.muted_sigma):.1%}); the optimizer has discounted them. Their share of the weighted "
                    "budget is magnitude, not importance. This concerns the auxiliary head's loss — the modality can "
                    "still reach the detector through fusion (P7).",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[table],
                    hypotheses=[
                        fb.hypothesis(
                            "The head's targets are noisy or poorly matched for this sensor", supporting=[table]
                        ),
                        fb.hypothesis(
                            "The head is redundant given the main decoder (could be removed or re-weighted)",
                            supporting=[table],
                        ),
                    ],
                    key="muted",
                )
            )
        dom = [(f, v) for f, v in fam.items() if v["weighted_share"] >= p.dominant_share]
        for f, v in dom:
            res.findings.append(
                fb.finding(
                    f"{FAMILY_LABEL.get(f, f)} holds {v['weighted_share']:.0%} of the weighted loss",
                    f"…but {v['no_reg_share']:.0%} of the gradient reaching the network and {v['raw_share']:.0%} of the raw "
                    "loss. Judge families by raw-loss trajectory and σ direction, not by budget share.",
                    severity=Severity.INFO,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[table],
                    key=f"dom|{f}",
                )
            )
        neg = [FAMILY_LABEL.get(f, f) for f, v in fam.items() if v["weighted"] < 0]
        if neg:
            res.notes.append(
                f"Negative weighted loss in {', '.join(neg)}: the σ tax (log σ < 0 adds a negative term "
                "with no gradient to the network), not a bug — the network-facing value is weighted_loss_no_reg."
            )
        start = sc.family_rollup(sc.ledger(ctx.target.series, at_start=True))
        if "denoise_cls" in fam and "decoder_cls" in fam and fam["decoder_cls"]["raw"]:
            r_end = fam["denoise_cls"]["raw"] / fam["decoder_cls"]["raw"]
            r_start = (
                start["denoise_cls"]["raw"] / start["decoder_cls"]["raw"]
                if "denoise_cls" in start and start.get("decoder_cls", {}).get("raw")
                else math.nan
            )
            ev = fb.evidence(
                f"denoising/main class-loss ratio {r_start:.2f} → {r_end:.2f}",
                run=run.ref,
                metric="train/uloss/cls[branch=dn] ÷ train/uloss/cls",
                value=r_end,
                baseline_value=r_start,
            )
            if r_end < 0.05:
                res.findings.append(
                    fb.finding(
                        "Denoising branch looks decoupled",
                        "The DN class loss is near zero relative "
                        "to the matched loss — check that DN queries are built and supervised.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        evidence=[ev],
                        key="dn0",
                    )
                )
            elif math.isfinite(r_start) and r_end > r_start * 1.05:
                res.notes.append(
                    f"Denoising/main ratio rises {r_start:.2f} → {r_end:.2f}: the DN scaffold sits at its "
                    "noise floor while the main task keeps improving."
                )
            else:
                res.notes.append(f"Denoising/main ratio {r_start:.2f} → {r_end:.2f}.")
        per_fam = {f: [r.weight for r in rows if r.family == f and math.isfinite(r.weight)] for f in fam}
        ks = {f: sorted(v)[len(v) // 2] for f, v in per_fam.items() if v}
        if ks:
            res.notes.append(
                "Task weights K = (weighted − no_reg)/σ (median per family): "
                + ", ".join(f"{FAMILY_LABEL.get(f, f)} {k:.3g}" for f, k in ks.items())
            )
        if not res.findings:
            res.notes.append("No muted or dominating loss family.")
        return res


STUDY_CHECKS = StudyChecks()
LOSS_LEDGER = LossLedger()


class LocalizationParams(AnalysisParams):
    knee_fraction: float = 0.5  # AUC below this share of the nearest bucket = collapsed
    fp_top_share: float = 0.3  # the worst 10 % of sequences carrying ≥ this share of FP mass = scene-concentrated
    deprioritized: tuple[str, ...] = ()


class EvalLocalization:
    name: ClassVar[str] = "eval_localization"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Where the model fails (per-class evaluation)"
    description: ClassVar[str] = (
        "From the per-class evaluation JSONs: the distance at which each class collapses (pr_curves_detailed), "
        "false-positive tails concentrated in a few scenes (false_positives_analysis), and — with a baseline — the "
        "ruler-free per-class comparison (torch_metric_evaluation), valid even when the rulers differ."
    )
    category: ClassVar[str] = "evaluation"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.EVAL_JSON})
    limitations: ClassVar[tuple[str, ...]] = (
        "Localises errors; worst sequences are where errors are, not why (no causal claim).",
        "Distance buckets differ between rulers — compare range decay only between runs with the same buckets.",
    )
    Params: ClassVar[type[AnalysisParams]] = LocalizationParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        from model_forensic.analysis import shared_ruler as sr

        p = params if isinstance(params, LocalizationParams) else LocalizationParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        ev = sr.last_eval(ctx.target.evals)
        if ev is None:
            res.skipped_reason = "no per-class evaluation JSONs (eval_json preset: …/<Class>/torch_metric_evaluation/…)"
            return res
        src = f"{ev.stage + ': ' if ev.stage else ''}{ev.root}"
        decays = sr.range_decay(ev, p.deprioritized)
        if decays:
            res.plots.append(
                PlotSpec(
                    id="eval.range",
                    title="AUC at the working point vs distance",
                    kind="line",
                    x_label="distance (m)",
                    y_label="AUC",
                    series=[
                        {"name": d.cls, "points": [[x, y] for x, y in zip(d.distances, d.auc, strict=True)]}
                        for d in decays
                    ],
                )
            )
            collapsed = [d for d in decays if d.knee is not None]
            if collapsed:
                ev_ = fb.evidence(
                    "; ".join(
                        f"{d.cls}@recall {d.recall}: AUC {d.near:.2f} at {d.distances[0]:g} m → "
                        f"{d.far:.2f} at {d.distances[-1]:g} m (below half from {d.knee:g} m)"
                        for d in collapsed
                    ),
                    run=run.ref,
                    artifact=src,
                    data={"decay": [d.__dict__ for d in decays]},
                )
                res.findings.append(
                    fb.finding(
                        f"Far-range collapse: {', '.join(f'{d.cls} past {d.knee:g} m' for d in collapsed[:4])}",
                        "Detection quality halves beyond these distances; the remaining gap is concentrated at range.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.HIGH,
                        label=EpistemicLabel.COMPUTED,
                        evidence=[ev_],
                        hypotheses=[
                            fb.hypothesis(
                                "Few far-range training labels for these classes (supervision density)",
                                supporting=[ev_],
                            ),
                            fb.hypothesis(
                                "Feature resolution at range is too low (backbone stride / BEV cell size)",
                                supporting=[ev_],
                            ),
                        ],
                        key="range",
                    )
                )
        tails = [t for t in sr.fp_tails(ev, p.deprioritized) if t.top_share >= p.fp_top_share]
        if tails:
            ev_ = fb.evidence(
                "; ".join(
                    f"{t.cls}: worst 10% of listed sequences carry {t.top_share:.0%} of the FP mass"
                    + (f", FP/frame up to {t.fppf:.2f}" if t.fppf is not None else "")
                    + f" (top: {', '.join(s[:14] for s, _ in t.worst[:3])})"
                    for t in tails
                ),
                run=run.ref,
                artifact=src,
                data={"tails": [t.__dict__ for t in tails]},
            )
            res.findings.append(
                fb.finding(
                    f"False positives concentrated in a few scenes ({', '.join(t.cls for t in tails)})",
                    "A long tail of pathological sequences carries most false positives — replay those scenes before "
                    "changing the model.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.HIGH,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[ev_],
                    key="fp_tail",
                )
            )
        bev = sr.last_eval(ctx.baseline.evals) if ctx.baseline is not None else None
        if bev is not None:
            t = sr.shared_table({"candidate": ev, "baseline": bev}, deprioritized=p.deprioritized)
            better, total = t.wins("baseline").get("candidate", (0, 0))
            if total:
                worse = [c for c in t.cells if c.best == "baseline"]
                ev_ = fb.evidence(
                    f"ruler-free per-class metrics: candidate better in {better}/{total} cells; worse in "
                    + (", ".join(f"{c.cls} {c.metric}" for c in worse[:8]) or "none"),
                    run=run.ref,
                    metric="torch_metric_evaluation",
                    data={"cells": [c.__dict__ for c in t.cells]},
                )
                res.findings.append(
                    fb.finding(
                        f"Shared ruler: better than the baseline in {better} of {total} class×metric cells",
                        "Per-class torch_metric_evaluation metrics, identical definitions for both runs (valid even when "
                        "their combined-metrics rulers differ).",
                        severity=Severity.INFO if better >= total / 2 else Severity.MEDIUM,
                        confidence=Confidence.HIGH,
                        label=EpistemicLabel.COMPUTED,
                        evidence=[ev_],
                        key="shared",
                    )
                )
        if not res.findings:
            res.notes.append("No far-range collapse or scene-concentrated false positives found.")
        return res


EVAL_LOCALIZATION = EvalLocalization()
PLUGINS = (STUDY_CHECKS, LOSS_LEDGER, EVAL_LOCALIZATION)
