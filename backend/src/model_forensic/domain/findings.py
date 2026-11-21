"""Structured findings: the contract between computation, interpretation and recommendation.

Epistemic labels make the separation explicit:

* OBSERVED     – retrieved verbatim from a backend (a logged value).
* COMPUTED     – deterministically derived by an analysis (a delta, a slope).
* INFERRED     – a rule-based reading of computed evidence ("training diverged").
* HYPOTHESIZED – a candidate cause; never asserted as fact.
* RECOMMENDED  – a proposed action.
* AI_GENERATED – produced by an LLM; always also carries one of the labels above.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import Field

from model_forensic.domain.base import SCHEMA_VERSION, DomainModel
from model_forensic.domain.run import RunRef


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return ["info", "low", "medium", "high", "critical"].index(self.value)


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EpistemicLabel(str, Enum):
    OBSERVED = "observed"
    COMPUTED = "computed"
    INFERRED = "inferred"
    HYPOTHESIZED = "hypothesized"
    RECOMMENDED = "recommended"
    AI_GENERATED = "ai_generated"


class EvidenceSource(DomainModel):
    run: RunRef | None = None
    metric: str | None = None
    artifact: str | None = None
    config_path: str | None = None
    step_range: tuple[float, float] | None = None


class Evidence(DomainModel):
    id: str  # stable within a finding set, e.g. "ev-3f2a…"; cited by AI
    label: EpistemicLabel
    statement: str
    source: EvidenceSource = Field(default_factory=EvidenceSource)
    value: float | None = None
    baseline_value: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)  # small plot-as-data payloads


class Hypothesis(DomainModel):
    id: str
    statement: str
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    confidence: Confidence = Confidence.LOW
    falsifiable_by: str | None = None
    label: EpistemicLabel = EpistemicLabel.HYPOTHESIZED


class ExperimentProposal(DomainModel):
    id: str
    hypothesis_ids: tuple[str, ...]
    title: str
    controlled_variable: str
    fixed_variables: tuple[str, ...] = ()
    expected_outcome: str
    measurements: tuple[str, ...] = ()
    acceptance_criterion: str
    estimated_cost_gpu_hours: float | None = None
    information_gain: float = 0.0  # heuristic in [0, 1]
    priority: int = 3  # 1 = highest
    label: EpistemicLabel = EpistemicLabel.RECOMMENDED


class Provenance(DomainModel):
    analysis: str
    analysis_version: str
    params_hash: str
    params: dict[str, Any] = Field(default_factory=dict)
    runs: tuple[RunRef, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    prompt_id: str | None = None
    prompt_version: int | None = None
    llm_model: str | None = None


class Finding(DomainModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    title: str
    statement: str
    category: str  # training_dynamics | loss | regression | config | evaluation | ...
    severity: Severity
    confidence: Confidence
    label: EpistemicLabel
    evidence: tuple[Evidence, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    recommendations: tuple[ExperimentProposal, ...] = ()
    limitations: tuple[str, ...] = ()
    provenance: Provenance
