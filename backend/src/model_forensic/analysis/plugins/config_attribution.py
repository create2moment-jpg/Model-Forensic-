"""Config attribution: link configuration changes to observed regressions as *hypotheses*.

This analysis never claims causation. With one baseline and one candidate, every changed
parameter is confounded with every other one; the output is a ranked list of candidate causes
plus the cheapest controlled experiments that would separate them.
"""

from __future__ import annotations

from typing import ClassVar

from model_forensic.analysis.base import (
    AnalysisContext,
    AnalysisParams,
    AnalysisResult,
    FindingBuilder,
    Requirement,
)
from model_forensic.analysis.comparison import compare
from model_forensic.domain.comparison import DeltaVerdict
from model_forensic.domain.config import ConfigChange
from model_forensic.domain.findings import Confidence, EpistemicLabel, Severity
from model_forensic.domain.metrics import MetricKey, Split

# Prior plausibility that a category of change affects a family/dimension of metrics.
_AFFINITY: dict[str, dict[str, float]] = {
    "modality": {"range": 0.9, "modality": 1.0, "detection": 0.7, "tracking": 0.5},
    "temporal": {"tracking": 0.9, "velocity": 0.9, "detection": 0.5, "occlusion": 0.8},
    "loss": {"detection": 0.7, "tracking": 0.5, "calibration": 0.8, "loss": 1.0},
    "uncertainty": {"calibration": 1.0, "detection": 0.4},
    "optimization": {"loss": 0.8, "detection": 0.6, "tracking": 0.5},
    "architecture": {"detection": 0.7, "tracking": 0.6},
    "assignment": {"detection": 0.8, "sparse": 1.0},
    "data": {"detection": 0.7, "class": 0.9, "range": 0.6},
    "evaluation": {"detection": 0.9, "tracking": 0.9},
    "randomness": {"detection": 0.3, "tracking": 0.3},
}


class Params(AnalysisParams):
    max_hypotheses: int = 5
    splits: list[str] = ["val", "test"]


class ConfigAttribution:
    name: ClassVar[str] = "config_attribution"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Configuration change attribution"
    description: ClassVar[str] = (
        "Ranks configuration changes as candidate causes of regressions and proposes single-variable "
        "ablations, ordered by information gain per GPU-hour."
    )
    category: ClassVar[str] = "config"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.BASELINE, Requirement.CONFIG_DIFF})
    limitations: ClassVar[tuple[str, ...]] = (
        "Correlation only: with a single baseline/candidate pair all changed parameters are confounded.",
        "Affinity priors between change categories and metric families are heuristic.",
    )
    Params: ClassVar[type[AnalysisParams]] = Params

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, Params) else Params.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        if ctx.baseline is None or ctx.config_diff is None:
            res.skipped_reason = "requires a baseline and a config diff"
            return res
        b, c, diff = ctx.baseline, ctx.target, ctx.config_diff
        fb = FindingBuilder(self, p, [b.run.ref, c.run.ref])
        changes = list(diff.meaningful)
        noise = [ch for ch in diff.changes if ch.is_noise]
        if not changes:
            res.notes.append(
                "No meaningful configuration differences"
                + (f" ({len(noise)} runtime-only changes ignored)" if noise else "")
            )
            cmp = compare(b, c, diff, ctx.registry, ctx.baseline_replicates, {Split(s) for s in p.splits})
            if cmp.regressions:
                ev = fb.evidence(
                    f"{len(cmp.regressions)} metric regression(s) with identical configuration",
                    run=c.run.ref,
                    value=float(len(cmp.regressions)),
                )
                res.findings.append(
                    fb.finding(
                        "Regression without configuration change",
                        "Metrics regressed although configurations are identical: suspect seed variance, code (commit) "
                        "changes, data version or environment drift.",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        evidence=[ev],
                        hypotheses=[
                            fb.hypothesis(
                                "Seed variance", supporting=[ev], falsifiable_by="Re-run both configs with 3 seeds"
                            ),
                            fb.hypothesis(
                                "Code change between commits",
                                supporting=[ev],
                                falsifiable_by=f"Diff commits {b.run.git.commit} vs {c.run.git.commit}",
                            ),
                        ],
                    )
                )
            return res

        cmp = compare(b, c, diff, ctx.registry, ctx.baseline_replicates, {Split(s) for s in p.splits})
        regs = cmp.regressions
        change_ev = {
            ch.path: fb.evidence(ch.describe(), label=EpistemicLabel.OBSERVED, run=c.run.ref, config_path=ch.path)
            for ch in changes
        }
        if not regs:
            ev = fb.evidence(f"{len(changes)} meaningful config change(s); no metric regressions", run=c.run.ref)
            res.findings.append(
                fb.finding(
                    "Configuration changed without regressions",
                    "No regression to attribute.",
                    severity=Severity.INFO,
                    confidence=Confidence.HIGH,
                    evidence=[ev, *change_ev.values()],
                )
            )
            return res

        reg_ev = [
            fb.evidence(
                f"{d.metric} {d.relative:+.1%}" if d.relative is not None else d.metric,
                run=c.run.ref,
                metric=d.metric,
                value=d.candidate,
                baseline_value=d.baseline,
            )
            for d in regs[:8]
        ]
        families: dict[str, float] = {}
        for d in regs:
            k = MetricKey.parse(d.metric)
            w = abs(d.relative or 0.05)
            families[ctx.registry.lookup(k.name).family] = families.get(ctx.registry.lookup(k.name).family, 0) + w
            for dim, _ in k.dims:
                families[dim] = families.get(dim, 0) + w

        scored: list[tuple[float, ConfigChange]] = []
        for ch in changes:
            aff = _AFFINITY.get(ch.category, {})
            score = sum(aff.get(f, 0.1) * w for f, w in families.items())
            if ch.relative_change is not None:
                score *= 1 + min(abs(ch.relative_change), 2.0) / 2
            elif ch.kind.value != "changed" or not isinstance(ch.baseline, (int, float)):
                score *= 1.5  # structural/categorical change (e.g. a modality added): treat as large
            scored.append((score, ch))
        scored.sort(key=lambda sc: (-sc[0], sc[1].path))
        top = scored[: p.max_hypotheses]
        norm = top[0][0] or 1.0
        cost = c.run.gpu_hours or b.run.gpu_hours

        hyps, props = [], []
        for i, (score, ch) in enumerate(top):
            rel_score = score / norm
            conf = Confidence.MEDIUM if rel_score > 0.75 and len(changes) <= 3 else Confidence.LOW
            h = fb.hypothesis(
                f"The change {ch.describe()} ({ch.category}) contributes to the regression",
                supporting=[change_ev[ch.path], *reg_ev[:3]],
                confidence=conf,
                falsifiable_by=f"Revert only {ch.path} on the candidate; if metrics do not recover, reject",
            )
            hyps.append(h)
            props.append(
                fb.proposal(
                    f"Ablate {ch.path}",
                    hypothesis_ids=(h.id,),
                    controlled_variable=ch.path,
                    fixed_variables=tuple(x.path for _, x in scored if x.path != ch.path)[:6] or ("all other config",),
                    expected_outcome=f"Reverting {ch.path} to {ch.baseline!r} recovers the regressed metrics if the hypothesis holds",
                    measurements=tuple(d.metric for d in regs[:5]),
                    acceptance_criterion="Recovery of >= 50% of the regression, beyond seed noise (>= 2 seeds per arm)",
                    estimated_cost_gpu_hours=cost,
                    information_gain=round(min(1.0, rel_score / max(1, len(changes)) * 2), 2),
                    priority=i + 1,
                )
            )

        n_conf = len(changes)
        res.findings.append(
            fb.finding(
                "Candidate causes from configuration changes",
                f"{len(regs)} regression(s) co-occur with {n_conf} meaningful config change(s). Ranked candidate causes "
                f"are listed as hypotheses; {'they are confounded and ' if n_conf > 1 else ''}none is established as causal.",
                severity=Severity.MEDIUM if n_conf > 1 else Severity.HIGH,
                confidence=Confidence.LOW if n_conf > 1 else Confidence.MEDIUM,
                label=EpistemicLabel.HYPOTHESIZED,
                evidence=[*reg_ev, *(change_ev[ch.path] for _, ch in top)],
                hypotheses=hyps,
                recommendations=props,
            )
        )
        if any(d.verdict == DeltaVerdict.REGRESSED for d in cmp.deltas) and noise:
            res.notes.append(f"Ignored {len(noise)} runtime-only config differences (paths, names, timestamps).")
        return res


PLUGIN = ConfigAttribution()
