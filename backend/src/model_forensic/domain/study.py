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

    def hypothesis(self, hid: str) -> StudyHypothesis:
        for h in self.hypotheses:
            if h.id == hid:
                return h
        raise KeyError(hid)
