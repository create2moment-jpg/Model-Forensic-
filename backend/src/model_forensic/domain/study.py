"""A Study: the record of investigating one experiment or a small group of experiments.

It holds the runs under study and their baseline, the hypotheses formed while studying them (each
with status, evidence, the architecture change it suggests and the run that tests it), notes, pinned
findings, AI investigations, and a timeline. The point is to keep the chain
observation → hypothesis → architecture change → verification run → verdict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import Field

from model_forensic.domain.base import SCHEMA_VERSION, DomainModel
from model_forensic.domain.run import RunRef


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HypothesisStatus(str, Enum):
    OPEN = "open"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class EvidenceKind(str, Enum):
    FINDING = "finding"  # deterministic finding id
    CLAIM = "claim"  # AI claim: "<investigation id>#<claim index>"
    METRIC = "metric"  # metric key on a run
    RUN = "run"
    NOTE = "note"


class EvidenceLink(DomainModel):
    kind: EvidenceKind
    ref: str
    label: str
    run: RunRef | None = None
    supports: bool = True  # False = evidence against the hypothesis


class StudyHypothesis(DomainModel):
    id: str
    statement: str
    status: HypothesisStatus = HypothesisStatus.OPEN
    rationale: str = ""
    evidence: tuple[EvidenceLink, ...] = ()
    proposed_change: str = ""  # the architecture / config change this hypothesis suggests
    reference_run: RunRef | None = None  # where the problem was observed (default: study baseline/first run)
    test_run: RunRef | None = None  # the run that implements the change
    verdict: str = ""
    source: str = "manual"  # manual | finding:<id> | claim:<inv>#<i>
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class StudyNote(DomainModel):
    id: str
    text: str
    created_at: datetime = Field(default_factory=_now)


class StudyEvent(DomainModel):
    at: datetime = Field(default_factory=_now)
    kind: str
    text: str


class Preregistration(DomainModel):
    """Written before looking at results: the claim, what decides it, what would falsify it, what to rule out first."""

    template: str = ""
    question: str = ""
    claim: str = ""
    deciding_metric: str = ""
    ruler: str | None = None  # combined-metrics ruler of the baseline at registration time (P1)
    falsified_if: str = ""
    confounders: tuple[str, ...] = ()
    confounders_checked: tuple[str, ...] = ()
    analyses: tuple[str, ...] = ()
    registered_at: datetime = Field(default_factory=_now)


class GateCheck(DomainModel):
    id: str
    title: str
    status: str  # FAIL | WARN | PASS | INFO | SKIP
    run: str
    detail: str


class Gate(DomainModel):
    """Pitfall checks over every run of the study; failing measurement checks block a verdict."""

    checked_at: datetime = Field(default_factory=_now)
    checks: tuple[GateCheck, ...] = ()
    blocking: tuple[str, ...] = ()  # "P1 on all runs", ...

    @property
    def open(self) -> bool:
        return not self.blocking


class DecisionOutcome(str, Enum):
    ADOPT = "adopt"
    REJECT = "reject"
    MORE_EVIDENCE = "more_evidence"
    INCONCLUSIVE = "inconclusive"


class Decision(DomainModel):
    outcome: DecisionOutcome
    decision: str
    rationale: str = ""
    based_on: tuple[str, ...] = ()  # hypothesis ids, finding ids, metric keys
    gate_override: str = ""  # why a blocked gate was overridden (empty when the gate was open)
    decided_at: datetime = Field(default_factory=_now)


class Study(DomainModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    title: str
    goal: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    runs: tuple[RunRef, ...] = ()
    baseline: RunRef | None = None
    hypotheses: tuple[StudyHypothesis, ...] = ()
    notes: tuple[StudyNote, ...] = ()
    pinned_findings: tuple[str, ...] = ()
    investigations: tuple[str, ...] = ()
    timeline: tuple[StudyEvent, ...] = ()
    preregistration: Preregistration | None = None
    gate: Gate | None = None
    decision: Decision | None = None

    def hypothesis(self, hid: str) -> StudyHypothesis:
        for h in self.hypotheses:
            if h.id == hid:
                return h
        raise KeyError(hid)
