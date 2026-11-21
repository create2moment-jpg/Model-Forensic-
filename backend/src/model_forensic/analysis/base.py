"""Analysis plugin contract.

Analyses receive a fully materialized :class:`AnalysisContext`: they never call backends.
This enforces the retrieval → computation boundary and makes every analysis deterministic and
replayable from stored inputs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel

from model_forensic.analysis.metric_registry import MetricRegistry, default_registry
from model_forensic.domain.config import ConfigDiff
from model_forensic.domain.findings import (
    Confidence,
    EpistemicLabel,
    Evidence,
    EvidenceSource,
    ExperimentProposal,
    Finding,
    Hypothesis,
    Provenance,
    Severity,
)
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.run import Run, RunRef
from model_forensic.shared.hashing import params_hash


class Requirement(str, Enum):
    SERIES = "series"
    BASELINE = "baseline"
    CONFIG_DIFF = "config_diff"
    CONFIG = "config"  # the run's own flat config
    LR = "lr"  # learning-rate schedule (logged metric, else the stage's Lightning metrics.csv)
    EVAL_TABLES = "eval_tables"  # per-sequence evaluation parquets (downloaded on demand, cached)
    EVAL_JSON = "eval_json"  # per-class evaluation JSONs (downloaded on demand, cached)


@dataclass(frozen=True)
class RunData:
    run: Run
    series: Mapping[MetricKey, MetricSeries]
    tables: Sequence[Any] = ()  # analysis.sequences.SequenceTable, only when a plugin requires EVAL_TABLES
    evals: Sequence[Any] = ()  # analysis.evaljson.EvalEvidence per evaluation (stage), when a plugin requires EVAL_JSON
    configs: Mapping[str, Any] = field(default_factory=dict)  # flat merged config, when a plugin requires CONFIG
    lr: MetricSeries | None = None  # when a plugin requires LR

    def find(self, name: str, split: str | None = None, dims: Mapping[str, str] | None = None) -> MetricSeries | None:
        want_dims = tuple(sorted((dims or {}).items()))
        for k, s in self.series.items():
            if k.name == name and k.dims == want_dims and (split is None or k.split.value == split):
                return s
        return None


@dataclass(frozen=True)
class AnalysisContext:
    target: RunData
    baseline: RunData | None = None
    baseline_replicates: Sequence[RunData] = ()
    config_diff: ConfigDiff | None = None
    registry: MetricRegistry = default_registry


class PlotSpec(BaseModel):
    """A plot as data: renderable by the UI and consumable by the AI context builder."""

    id: str
    title: str
    kind: str = "line"  # line | bar | scatter
    x_label: str = "step"
    y_label: str = ""
    series: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    log_y: bool = False


@dataclass
class AnalysisResult:
    analysis: str
    findings: list[Finding] = field(default_factory=list)
    plots: list[PlotSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


class AnalysisParams(BaseModel):
    model_config = {"extra": "forbid"}


@runtime_checkable
class AnalysisPlugin(Protocol):
    name: ClassVar[str]
    version: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str]
    requires: ClassVar[frozenset[Requirement]]
    limitations: ClassVar[tuple[str, ...]]
    Params: ClassVar[type[AnalysisParams]]

    def run(self, ctx: AnalysisContext, params: AnalysisParams) -> AnalysisResult: ...


class FindingBuilder:
    """Helper that assigns deterministic ids and fills provenance consistently."""

    def __init__(self, plugin: AnalysisPlugin, params: AnalysisParams, runs: Sequence[RunRef]) -> None:
        self._plugin = plugin
        self._params = params.model_dump()
        self._runs = tuple(runs)
        self._phash = params_hash(self._params)

    def _id(self, prefix: str, *parts: object) -> str:
        raw = "|".join([self._plugin.name, self._plugin.version, self._phash, *map(str, self._runs), *map(str, parts)])
        return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:10]}"

    def evidence(
        self,
        statement: str,
        *,
        label: EpistemicLabel = EpistemicLabel.COMPUTED,
        run: RunRef | None = None,
        metric: str | None = None,
        artifact: str | None = None,
        config_path: str | None = None,
        step_range: tuple[float, float] | None = None,
        value: float | None = None,
        baseline_value: float | None = None,
        data: dict[str, Any] | None = None,
    ) -> Evidence:
        return Evidence(
            id=self._id("ev", statement, metric, config_path, run),
            label=label,
            statement=statement,
            source=EvidenceSource(
                run=run, metric=metric, artifact=artifact, config_path=config_path, step_range=step_range
            ),
            value=value,
            baseline_value=baseline_value,
            data=data or {},
        )

    def hypothesis(
        self,
        statement: str,
        *,
        supporting: Sequence[Evidence] = (),
        confidence: Confidence = Confidence.LOW,
        falsifiable_by: str | None = None,
        contradicting: Sequence[Evidence] = (),
    ) -> Hypothesis:
        return Hypothesis(
            id=self._id("hy", statement),
            statement=statement,
            supporting_evidence=tuple(e.id for e in supporting),
            contradicting_evidence=tuple(e.id for e in contradicting),
            confidence=confidence,
            falsifiable_by=falsifiable_by,
        )

    def proposal(self, title: str, **kw: Any) -> ExperimentProposal:
        return ExperimentProposal(id=self._id("ex", title), title=title, **kw)

    def finding(
        self,
        title: str,
        statement: str,
        *,
        severity: Severity,
        confidence: Confidence,
        label: EpistemicLabel = EpistemicLabel.INFERRED,
        evidence: Sequence[Evidence] = (),
        hypotheses: Sequence[Hypothesis] = (),
        recommendations: Sequence[ExperimentProposal] = (),
        limitations: Sequence[str] = (),
        key: str = "",
    ) -> Finding:
        return Finding(
            id=self._id("fd", title, key),
            title=title,
            statement=statement,
            category=self._plugin.category,
            severity=severity,
            confidence=confidence,
            label=label,
            evidence=tuple(evidence),
            hypotheses=tuple(hypotheses),
            recommendations=tuple(recommendations),
            limitations=tuple(self._plugin.limitations) + tuple(limitations),
            provenance=Provenance(
                analysis=self._plugin.name,
                analysis_version=self._plugin.version,
                params_hash=self._phash,
                params=self._params,
                runs=self._runs,
            ),
        )


def fmt(v: float | None, digits: int = 4) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}g}"
