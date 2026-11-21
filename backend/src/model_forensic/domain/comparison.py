from __future__ import annotations

from enum import Enum

from model_forensic.domain.base import DomainModel
from model_forensic.domain.config import ConfigDiff
from model_forensic.domain.run import RunRef


class DeltaVerdict(str, Enum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"  # within noise / meaningful threshold
    UNKNOWN = "unknown"  # direction unknown for this metric


class MetricDelta(DomainModel):
    metric: str
    baseline: float
    candidate: float
    absolute: float
    relative: float | None
    verdict: DeltaVerdict
    higher_is_better: bool | None
    # Standardized effect vs. baseline replicate spread when replicates exist.
    z_score: float | None = None
    noise_floor: float | None = None
    significance_basis: str = "threshold"  # "threshold" | "replicates"


class Comparison(DomainModel):
    baseline: RunRef
    candidate: RunRef
    deltas: tuple[MetricDelta, ...]
    config_diff: ConfigDiff
    baseline_replicates: tuple[RunRef, ...] = ()
    duration_ratio: float | None = None

    @property
    def regressions(self) -> list[MetricDelta]:
        return [d for d in self.deltas if d.verdict == DeltaVerdict.REGRESSED]

    @property
    def improvements(self) -> list[MetricDelta]:
        return [d for d in self.deltas if d.verdict == DeltaVerdict.IMPROVED]
