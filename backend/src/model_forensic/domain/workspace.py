from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field

from model_forensic.domain.base import SCHEMA_VERSION, DomainModel
from model_forensic.domain.run import RunRef


class WorkspaceNote(DomainModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    text: str


class ForensicWorkspace(DomainModel):
    """A persistent investigation: runs, baseline, findings, notes, conversations and reports."""

    schema_version: int = SCHEMA_VERSION
    id: str
    title: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    runs: tuple[RunRef, ...] = ()
    baseline: RunRef | None = None
    finding_ids: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    notes: tuple[WorkspaceNote, ...] = ()
    conversation_ids: tuple[str, ...] = ()
    report_ids: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
