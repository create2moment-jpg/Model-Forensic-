"""Pipeline stage deltas: what changed between evaluation stages of one pipeline (e.g. pretrain → finetune)."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import ClassVar

from model_forensic.analysis.base import (
    AnalysisContext,
    AnalysisParams,
    AnalysisResult,
    FindingBuilder,
    PlotSpec,
    Requirement,
)
from model_forensic.analysis.plugins.architecture import _tail_mean
from model_forensic.domain.findings import Confidence, EpistemicLabel, Evidence, Severity
from model_forensic.domain.metrics import MetricKey, Split


class StageDeltaParams(AnalysisParams):
    min_rel_change: float = 0.02
    max_listed: int = 8
    deprioritized: tuple[str, ...] = ()  # class names that must not drive the verdict (team policy)


def _fmt_key(k: MetricKey) -> str:
    rest = ",".join(f"{a}={b}" for a, b in k.dims if a not in {"stage", "task"})
    return f"{k.name}[{rest}]" if rest else k.name


class StageDelta:
    name: ClassVar[str] = "stage_delta"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Pipeline stage deltas"
    description: ClassVar[str] = (
        "For pipelines that evaluate after several stages (pretrain → finetune): which metrics, classes and "
        "distance buckets improved or regressed between the first and the last evaluation."
    )
    category: ClassVar[str] = "evaluation"
    requires: ClassVar[frozenset[Requirement]] = frozenset({Requirement.SERIES})
    limitations: ClassVar[tuple[str, ...]] = (
        "Compares final values of one run's stages; seed noise is unknown without replicate pipelines.",
    )
    Params: ClassVar[type[AnalysisParams]] = StageDeltaParams

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult:
        p = params if isinstance(params, StageDeltaParams) else StageDeltaParams.model_validate(params.model_dump())
        res = AnalysisResult(analysis=self.name)
        run = ctx.target.run
        fb = FindingBuilder(self, p, [run.ref])
        groups: dict[MetricKey, dict[tuple[int, str], float]] = defaultdict(dict)
        for k, s in ctx.target.series.items():
            d = k.dim_dict
            if "stage" not in d or k.split not in (Split.VAL, Split.TEST, Split.UNKNOWN):
                continue
            base = MetricKey(name=k.name, split=k.split, dims=tuple((a, b) for a, b in k.dims if a != "stage"))
            idx = int(s.provenance.get("stage_index", "0") or 0)
            v = _tail_mean(s, 0.01)
            if math.isfinite(v):
                groups[base][(idx, d["stage"])] = v
        groups = {k: v for k, v in groups.items() if len(v) >= 2}
        if not groups:
            res.skipped_reason = (
                "no metric logged by more than one pipeline stage (needs a pipeline with ≥2 evaluations)."
            )
            return res
        first_stage = min(min(v) for v in groups.values())[1]
        last_stage = max(max(v) for v in groups.values())[1]
        changes = []
        for k, vals in groups.items():
            (_, a), (_, b) = min(vals.items())[0], max(vals.items())[0]
            va, vb = vals[min(vals)], vals[max(vals)]
            hib = ctx.registry.lookup(k.name).higher_is_better
            if hib is None or va == 0:
                continue
            rel = (vb - va) / abs(va)
            good = rel if hib else -rel
            changes.append((good, k, va, vb, a, b))
        if not changes:
            res.skipped_reason = "stage metrics have no known direction (register them in the metric registry)."
            return res
        depri = {c.lower() for c in p.deprioritized}

        def is_depri(k: MetricKey) -> bool:
            return k.dim_dict.get("class", "").lower() in depri

        worse = sorted((c for c in changes if c[0] < -p.min_rel_change), key=lambda c: c[0])
        better = sorted((c for c in changes if c[0] > p.min_rel_change), key=lambda c: -c[0])
        prio_worse = [c for c in worse if not is_depri(c[1])]
        title_stages = f"{first_stage} → {last_stage}"
        summary = fb.evidence(
            f"{len(changes)} metrics evaluated in several stages ({title_stages}): {len(better)} better, "
            f"{len(worse)} worse by more than {p.min_rel_change:.0%}",
            run=run.ref,
            metric="val/*[stage=*]",
            data={"better": len(better), "worse": len(worse), "total": len(changes)},
        )
        res.plots.append(
            PlotSpec(
                id="stage_delta.changes",
                title=f"Change {title_stages} (positive = better)",
                kind="bar",
                x_label="metric",
                y_label="relative change (direction-aware)",
                series=[{"name": "change", "points": [[_fmt_key(c[1]), c[0]] for c in (better[:10] + worse[:10])]}],
            )
        )

        def ev(c: tuple[float, MetricKey, float, float, str, str]) -> Evidence:
            good, k, va, vb, a, b = c
            return fb.evidence(
                f"{_fmt_key(k)}: {a} {va:.4g} → {b} {vb:.4g} ({good:+.1%}, direction-aware)",
                run=run.ref,
                metric=str(k),
                value=vb,
                baseline_value=va,
            )

        if prio_worse:
            evs = [summary] + [ev(c) for c in prio_worse[: p.max_listed]]
            worst = prio_worse[0]
            res.findings.append(
                fb.finding(
                    f"{len(prio_worse)} metric(s) got worse from {first_stage} to {last_stage}",
                    f"The later stage regressed {_fmt_key(worst[1])} by {-worst[0]:.1%}"
                    + (f" and {len(prio_worse) - 1} more" if len(prio_worse) > 1 else "")
                    + ".",
                    severity=Severity.HIGH if worst[0] < -0.1 else Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=evs,
                    hypotheses=[
                        fb.hypothesis(
                            f"{last_stage} data/objective shifts the model away from these classes or ranges",
                            supporting=evs[1:2],
                        ),
                        fb.hypothesis(
                            "Catastrophic forgetting during the later stage (learning rate, frozen layers)",
                            supporting=evs[1:2],
                        ),
                    ],
                    key="worse",
                )
            )
        if better:
            res.findings.append(
                fb.finding(
                    f"{len(better)} metric(s) improved from {first_stage} to {last_stage}",
                    f"Largest gain: {_fmt_key(better[0][1])} {better[0][0]:+.1%}.",
                    severity=Severity.INFO,
                    confidence=Confidence.MEDIUM,
                    label=EpistemicLabel.COMPUTED,
                    evidence=[summary] + [ev(c) for c in better[: p.max_listed]],
                    key="better",
                )
            )
        depri_worse = [c for c in worse if is_depri(c[1])]
        if depri_worse:
            res.notes.append(
                f"{len(depri_worse)} regression(s) only in de-prioritised classes "
                f"({', '.join(sorted({c[1].dim_dict.get('class', '') for c in depri_worse}))}) — not counted."
            )
        return res


STAGE_DELTA = StageDelta()
PLUGINS = (STAGE_DELTA,)
