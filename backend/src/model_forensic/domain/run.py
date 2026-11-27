"""The normalized Run: the single shape every backend connector must produce."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from model_forensic.domain.base import SCHEMA_VERSION, DomainModel


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}


class RunKind(str, Enum):
    TRAINING = "training"
    EVALUATION = "evaluation"
    PIPELINE = "pipeline"
    SWEEP = "sweep"
    BATCH = "batch"
    OTHER = "other"


class RunRef(DomainModel):
    """Globally unique reference: the same run id may exist in two backends."""

    backend: str
    run_id: str

    def __str__(self) -> str:
        return f"{self.backend}:{self.run_id}"

    @classmethod
    def parse(cls, value: str, default_backend: str = "local") -> RunRef:
        backend, sep, rid = value.partition(":")
        return cls(backend=backend, run_id=rid) if sep else cls(backend=default_backend, run_id=value)


class GitInfo(DomainModel):
    commit: str | None = None
    branch: str | None = None
    repository: str | None = None
    dirty: bool | None = None


class ComputeInfo(DomainModel):
    target: str | None = None
    instance_type: str | None = None
    gpu_type: str | None = None
    gpu_count: int | None = None
    node_count: int | None = None


class DatasetReference(DomainModel):
    name: str
    version: str | None = None
    uri: str | None = None
    role: str = "input"  # input | train | val | test


class ModelReference(DomainModel):
    name: str
    version: str | None = None
    uri: str | None = None


class JobIO(DomainModel):
    """One input or output of a job, as studio lists it: an asset (name + version), a path, or a literal value."""

    name: str
    direction: str = "input"  # input | output
    type: str | None = None  # uri_folder | uri_file | mltable | custom_model | literal …
    asset: str | None = None  # the data / model asset's name, when the path names one
    version: str | None = None
    path: str | None = None
    mode: str | None = None  # ro_mount | download | rw_mount | upload …
    value: str | None = None  # a literal input (number, string)


class FailureInfo(DomainModel):
    message: str
    category: str | None = None  # oom | nan | timeout | user_error | infra | unknown
    log_excerpt: str | None = None


class Run(DomainModel):
    schema_version: int = SCHEMA_VERSION
    ref: RunRef
    name: str
    display_name: str | None = None
    experiment: str | None = None
    kind: RunKind = RunKind.OTHER
    status: RunStatus = RunStatus.UNKNOWN
    created_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    user: str | None = None
    description: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    environment: str | None = None
    git: GitInfo = Field(default_factory=GitInfo)
    compute: ComputeInfo = Field(default_factory=ComputeInfo)
    datasets: tuple[DatasetReference, ...] = ()
    models: tuple[ModelReference, ...] = ()
    parent: RunRef | None = None
    children: tuple[RunRef, ...] = ()
    failure: FailureInfo | None = None
    # Everything else the job carries, as studio shows it: its properties (secret-looking values hidden) and its
    # inputs / outputs with their assets and versions.
    properties: dict[str, str] = Field(default_factory=dict)
    io: tuple[JobIO, ...] = ()
    # Summary scalars (final value per metric) for fast list/search views.
    summary_metrics: dict[str, float] = Field(default_factory=dict)
    # Backend-specific leftovers. Analysis code must never read this.
    backend_extras: dict[str, str] = Field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds()
        return None

    @property
    def gpu_hours(self) -> float | None:
        d = self.duration_s
        if d is None:
            return None
        gpus = (self.compute.gpu_count or 0) * (self.compute.node_count or 1)
        return d / 3600 * gpus if gpus else None
