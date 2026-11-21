from __future__ import annotations

from datetime import datetime
from enum import Enum

from model_forensic.domain.base import DomainModel
from model_forensic.domain.run import RunRef


class ArtifactKind(str, Enum):
    LOG = "log"
    METRICS = "metrics"
    CONFIG = "config"
    CHECKPOINT = "checkpoint"
    EVALUATION = "evaluation"
    PREDICTIONS = "predictions"
    MODEL = "model"
    ENVIRONMENT = "environment"
    OTHER = "other"


class Artifact(DomainModel):
    run: RunRef
    path: str  # relative, POSIX, as reported by the backend (untrusted)
    kind: ArtifactKind = ArtifactKind.OTHER
    size_bytes: int | None = None
    sha256: str | None = None
    etag: str | None = None
    modified_at: datetime | None = None


class Checkpoint(DomainModel):
    artifact: Artifact
    step: int | None = None
    epoch: int | None = None
    is_best: bool = False
    metrics_at_save: dict[str, float] = {}
