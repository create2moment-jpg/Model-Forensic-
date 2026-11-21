"""Regression forensics: detect, rank and localize regressions of a candidate vs. a baseline."""

from __future__ import annotations

from collections import defaultdict
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
from model_forensic.analysis.comparison import compare
from model_forensic.domain.comparison import DeltaVerdict, MetricDelta
from model_forensic.domain.config import ConfigDiff
from model_forensic.domain.findings import Confidence, EpistemicLabel, Evidence, Severity
from model_forensic.domain.metrics import MetricKey, Split


class RegressionParams(AnalysisParams):
    splits: list[str] = ["val", "test"]
    headline_metrics: list[str] = ["NDS", "mAP", "AMOTA", "AMOTP"]
    high_rel: float = 0.05
    medium_rel: float = 0.02
    localization_ratio: float = 2.0


class RegressionForensics:
    name: ClassVar[str] = "regression"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Regression forensics"
    description: ClassVar[str] = (
        "Compares candidate vs. baseline on evaluation metrics, ranks regressions by severity, "
        "uses baseline replicates (other seeds) as a noise floor when available, and localizes "
        "regressions along metric dimensions (range, class, modality, ...)."
    )
    category: ClassVar[str] = "regression"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES, Requirement.BASELINE})
    limitations: ClassVar[tuple[str, ...]] = ()
    Params: ClassVar[type[AnalysisParams]] = RegressionParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, RegressionParams) else RegressionParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        if ctx.baseline is None:
            res.skipped_reason = "requires a baseline run"
            return res
        b, c = ctx.baseline, ctx.target
        fb = FindingBuilder(self, p, [b.run.ref, c.run.ref, *(r.run.ref for r in ctx.baseline_replicates)])
        diff = ctx.config_diff or ConfigDiff(baseline=b.run.ref, candidate=c.run.ref)
        cmp = compare(b, c, diff, ctx.registry, ctx.baseline_replicates, {Split(s) for s in p.splits})
        limitations: list[str] = []
        if len(ctx.baseline_replicates) < 2:
            limitations.append(
                "Fewer than 2 baseline replicates: significance uses fixed relative thresholds, not measured "
                "seed variance. Small deltas may be noise."
            )

        top = [d for d in cmp.deltas if not MetricKey.parse(d.metric).dims]
        res.plots.append(
            PlotSpec(
                id="regression.deltas",
                title="Relative change vs baseline",
                kind="bar",
                x_label="metric",
                y_label="relative change",
                series=[{"name": "relative", "points": [[d.metric, round(d.relative or 0.0, 5)] for d in top]}],
            )
        )

        sliced: dict[MetricKey, list[tuple[MetricKey, MetricDelta]]] = defaultdict(list)
        for d in cmp.deltas:
            k = MetricKey.parse(d.metric)
            if k.dims:
                sliced[k.base].append((k, d))

        for d in sorted(top, key=lambda d: -(abs(d.relative or 0.0))):
            if d.verdict != DeltaVerdict.REGRESSED:
                continue
            key = MetricKey.parse(d.metric)
            ev = [
                fb.evidence(
                    f"{d.metric}: {fmt(d.baseline)} → {fmt(d.candidate)} ({_rel(d)})"
                    + (
                        f", z={d.z_score:.1f} vs {len(cmp.baseline_replicates) + 1} baseline seeds"
                        if d.z_score is not None
                        else ""
                    ),
                    run=c.run.ref,
                    metric=d.metric,
                    value=d.candidate,
                    baseline_value=d.baseline,
                )
            ]
            sev = self._severity(d, key.name in p.headline_metrics, p)
            loc_text, loc_ev = self._localize(fb, c, key, sliced.get(key, []), p)
            ev.extend(loc_ev)
            res.findings.append(
                fb.finding(
                    f"Regression in {d.metric}" + (f" ({loc_text})" if loc_text else ""),
                    f"{d.metric} regressed by {_rel(d)} relative to the baseline"
                    + (f"; {loc_text}." if loc_text else "."),
                    severity=sev,
                    confidence=Confidence.HIGH if d.significance_basis == "replicates" else Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=ev,
                    limitations=limitations,
                    key=d.metric,
                )
            )

        # Regressions visible only in slices (hidden regressions): headline flat, a slice degrades.
        for base, items in sliced.items():
            parent = next((d for d in top if MetricKey.parse(d.metric) == base), None)
            if parent is not None and parent.verdict == DeltaVerdict.REGRESSED:
                continue
            worst = [(k, d) for k, d in items if d.verdict == DeltaVerdict.REGRESSED]
            if not worst:
                continue
            k, d = max(worst, key=lambda kd: abs(kd[1].relative or 0.0))
            if abs(d.relative or 0.0) < p.medium_rel:
                continue
            hidden_ev = fb.evidence(
                f"{k}: {fmt(d.baseline)} → {fmt(d.candidate)} ({_rel(d)}) while aggregate {base} is "
                f"{'unchanged' if parent is None or parent.verdict == DeltaVerdict.UNCHANGED else parent.verdict.value}",
                run=c.run.ref,
                metric=str(k),
                value=d.candidate,
                baseline_value=d.baseline,
            )
            res.findings.append(
                fb.finding(
                    f"Hidden regression in {base} slice {','.join(f'{a}={b}' for a, b in k.dims)}",
                    f"The aggregate {base} masks a {_rel(d)} regression in the {','.join(f'{a}={b}' for a, b in k.dims)} slice.",
                    severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[hidden_ev],
                    limitations=limitations,
                    key=str(k),
                )
            )

        if not any(d.verdict == DeltaVerdict.REGRESSED for d in cmp.deltas):
            res.notes.append("No regressions beyond the significance threshold.")
        return res

    @staticmethod
    def _severity(d: MetricDelta, headline: bool, p: RegressionParams) -> Severity:
        r = abs(d.relative or 0.0)
        sev = Severity.HIGH if r >= p.high_rel else Severity.MEDIUM if r >= p.medium_rel else Severity.LOW
        if headline and sev == Severity.MEDIUM:
            sev = Severity.HIGH
        if headline and r >= 2 * p.high_rel:
            sev = Severity.CRITICAL
        return sev

    @staticmethod
    def _localize(
        fb: FindingBuilder, c: RunData, key: MetricKey, items: list[tuple[MetricKey, MetricDelta]], p: RegressionParams
    ) -> tuple[str, list]:  # type: ignore[type-arg]
        """For each dimension, check whether the regression concentrates in one value."""
        if not items:
            return "", []
        by_dim: dict[str, list[tuple[str, MetricDelta]]] = defaultdict(list)
        for k, d in items:
            if len(k.dims) == 1:
                by_dim[k.dims[0][0]].append((k.dims[0][1], d))
        texts: list[str] = []
        evs: list[Evidence] = []
        for dim, vals in by_dim.items():
            if len(vals) < 2:
                continue
            regress = sorted(vals, key=lambda vd: _badness(vd[1]), reverse=True)
            worst_v, worst_d = regress[0]
            rest = [_badness(d) for _, d in regress[1:]]
            rest_mean = sum(rest) / len(rest)
            wb = _badness(worst_d)
            if wb > 0 and (rest_mean <= 0 or wb / max(rest_mean, 1e-12) >= p.localization_ratio):
                texts.append(f"concentrated in {dim}={worst_v}")
                evs.append(
                    fb.evidence(
                        f"{key.name} by {dim}: " + ", ".join(f"{v} {_rel(d)}" for v, d in sorted(vals)),
                        run=c.run.ref,
                        metric=f"{key}[{dim}=*]",
                        data={"slices": {v: d.relative for v, d in vals}},
                    )
                )
        return "; ".join(texts), evs


def _badness(d: MetricDelta) -> float:
    """Positive when worse, regardless of metric direction."""
    rel = d.relative or 0.0
    if d.higher_is_better is None:
        return 0.0
    return -rel if d.higher_is_better else rel


def _rel(d: MetricDelta) -> str:
    return f"{d.relative:+.1%}" if d.relative is not None else f"{d.absolute:+.4g}"


PLUGIN = RegressionForensics()
