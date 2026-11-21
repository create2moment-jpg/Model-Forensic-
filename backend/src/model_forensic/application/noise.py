"""Replicate statistics: the noise floor of each metric across seeds, and effect sizes against it."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from model_forensic.application.service import ForensicService
from model_forensic.domain.metrics import Split
from model_forensic.domain.run import RunRef


@dataclass
class NoiseRow:
    metric: str
    n: int
    mean: float
    std: float
    cv: float  # std / |mean|


@dataclass
class EffectRow:
    metric: str
    baseline_mean: float
    baseline_std: float
    n: int
    candidate: float
    delta: float
    z: float | None  # delta / std
    d: float | None  # standardized effect (Cohen's d with the candidate as one observation)
    ci95: tuple[float, float] | None  # 95 % interval of the baseline mean
    beyond_noise: bool


def _final(svc: ForensicService, ref: RunRef, splits: set[Split]) -> dict[str, float]:
    out = {}
    for s in svc.series(ref):
        if s.key.split in splits and "stage" not in s.key.dim_dict and len(s):
            v = s.aggregate()
            if math.isfinite(v):
                out[str(s.key)] = v
    return out


def noise_floor(svc: ForensicService, runs: Sequence[RunRef | str], splits: set[Split] | None = None) -> list[NoiseRow]:
    sp = splits or {Split.VAL, Split.TEST}
    finals = [_final(svc, svc.ref(r), sp) for r in runs]
    keys = set.intersection(*(set(f) for f in finals)) if finals else set()
    rows = []
    for k in sorted(keys):
        vals = [f[k] for f in finals]
        n = len(vals)
        mean = sum(vals) / n
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
        rows.append(NoiseRow(k, n, mean, std, std / abs(mean) if mean else math.inf))
    return rows


def effects(
    svc: ForensicService, baselines: Sequence[RunRef | str], candidate: RunRef | str, z_threshold: float = 2.0
) -> list[EffectRow]:
    floor = {r.metric: r for r in noise_floor(svc, baselines)}
    cand = _final(svc, svc.ref(candidate), {Split.VAL, Split.TEST})
    out = []
    for k, nr in floor.items():
        if k not in cand:
            continue
        delta = cand[k] - nr.mean
        z = delta / nr.std if nr.std > 0 else None
        ci = (
            (nr.mean - 1.96 * nr.std / math.sqrt(nr.n), nr.mean + 1.96 * nr.std / math.sqrt(nr.n)) if nr.n > 1 else None
        )
        out.append(
            EffectRow(k, nr.mean, nr.std, nr.n, cand[k], delta, z, z, ci, z is not None and abs(z) >= z_threshold)
        )
    return sorted(out, key=lambda e: -(abs(e.z) if e.z is not None else 0))
