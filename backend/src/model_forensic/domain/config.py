"""Configuration snapshots and semantic diffs."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from model_forensic.domain.base import DomainModel
from model_forensic.domain.run import RunRef


class ConfigurationSnapshot(DomainModel):
    run: RunRef
    source: str  # file name or "params"
    data: dict[str, Any] = Field(default_factory=dict)


class ChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class ConfigChange(DomainModel):
    path: str
    kind: ChangeKind
    baseline: Any = None
    candidate: Any = None
    category: str = "other"  # optimization | temporal | modality | loss | architecture | data | ...
    relative_change: float | None = None
    is_noise: bool = False  # e.g. output dirs, timestamps, run ids

    def describe(self) -> str:
        if self.kind == ChangeKind.ADDED:
            return f"{self.path}: (absent) → {self.candidate!r}"
        if self.kind == ChangeKind.REMOVED:
            return f"{self.path}: {self.baseline!r} → (absent)"
        rel = f" ({self.relative_change:+.0%})" if self.relative_change is not None else ""
        return f"{self.path}: {self.baseline!r} → {self.candidate!r}{rel}"


class ConfigDiff(DomainModel):
    baseline: RunRef
    candidate: RunRef
    changes: tuple[ConfigChange, ...] = ()

    @property
    def meaningful(self) -> tuple[ConfigChange, ...]:
        return tuple(c for c in self.changes if not c.is_noise)

    def by_category(self) -> dict[str, list[ConfigChange]]:
        out: dict[str, list[ConfigChange]] = {}
        for c in self.meaningful:
            out.setdefault(c.category, []).append(c)
        return out
