"""Pure comparison computation: summary deltas with noise-aware verdicts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

from model_forensic.analysis.base import RunData
from model_forensic.analysis.metric_registry import MetricRegistry
from model_forensic.domain.comparison import Comparison, DeltaVerdict, MetricDelta
from model_forensic.domain.config import ConfigDiff
from model_forensic.domain.metrics import Aggregation, MetricKey, Split

Z_THRESHOLD = 2.0


def summarize(data: RunData, splits: set[Split] | None = None) -> dict[MetricKey, float]:
    out: dict[MetricKey, float] = {}
    for key, s in data.series.items():
        if splits is not None and key.split not in splits:
            continue
        v = s.aggregate(Aggregation.LAST)
        if math.isfinite(v):
            out[key] = v
    return out


def metric_delta(
    key: MetricKey,
    base: float,
    cand: float,
    registry: MetricRegistry,
    replicate_values: Sequence[float] = (),
) -> MetricDelta:
    desc = registry.lookup(key.name)
    absolute = cand - base
    relative = absolute / abs(base) if base != 0 else None
    z = noise = None
    basis = "threshold"
    reps = [v for v in replicate_values if math.isfinite(v)]
    spread = float(np.std(np.asarray([base, *reps]), ddof=1)) if len(reps) >= 2 else 0.0
    if len(reps) >= 2 and spread > 1e-12 * max(1.0, abs(base)):
        noise = spread
        basis = "replicates"
        z = absolute / noise
        # Require both statistical and practical significance.
        meaningful = (
            abs(z) >= Z_THRESHOLD
            and abs(relative if relative is not None else absolute) >= desc.min_meaningful_rel_change / 2
        )
    else:
        meaningful = abs(relative if relative is not None else absolute) >= desc.min_meaningful_rel_change
    if desc.higher_is_better is None:
        verdict = DeltaVerdict.UNKNOWN if meaningful else DeltaVerdict.UNCHANGED
    elif not meaningful:
        verdict = DeltaVerdict.UNCHANGED
    else:
        better = absolute > 0 if desc.higher_is_better else absolute < 0
        verdict = DeltaVerdict.IMPROVED if better else DeltaVerdict.REGRESSED
    return MetricDelta(
        metric=str(key),
        baseline=base,
        candidate=cand,
        absolute=absolute,
        relative=relative,
        verdict=verdict,
        higher_is_better=desc.higher_is_better,
        z_score=z,
        noise_floor=noise,
        significance_basis=basis,
    )


def compare(
    baseline: RunData,
    candidate: RunData,
    config_diff: ConfigDiff,
    registry: MetricRegistry,
    replicates: Sequence[RunData] = (),
    splits: set[Split] | None = None,
) -> Comparison:
    b, c = summarize(baseline, splits), summarize(candidate, splits)
    rep_summaries: list[Mapping[MetricKey, float]] = [summarize(r, splits) for r in replicates]
    deltas = [
        metric_delta(k, b[k], c[k], registry, [r[k] for r in rep_summaries if k in r])
        for k in sorted(set(b) & set(c), key=str)
    ]
    db, dc = baseline.run.duration_s, candidate.run.duration_s
    return Comparison(
        baseline=baseline.run.ref,
        candidate=candidate.run.ref,
        deltas=tuple(deltas),
        config_diff=config_diff,
        baseline_replicates=tuple(r.run.ref for r in replicates),
        duration_ratio=(dc / db) if db and dc else None,
    )
