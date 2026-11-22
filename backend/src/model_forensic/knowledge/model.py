"""Knowledge entries (schema mirrored in ``harness/knowledge.schema.json``)."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Kind = Literal["constraint", "fact", "setup", "caveat", "code_ref"]
Status = Literal["proposed", "active", "retired"]
Confidence = Literal["low", "medium", "high"]


class When(BaseModel):
    """All given conditions must hold; an empty ``when`` always applies. Matching is case-insensitive."""

    model_config = ConfigDict(extra="forbid")
    setup: list[str] = Field(default_factory=list, description="any of these setup labels (e.g. 1v5r, 5v1L)")
    dataset_contains: list[str] = Field(default_factory=list, description="any substring of a dataset name")
    stage_contains: list[str] = Field(default_factory=list, description="any substring of the stage / run name")
    config: dict[str, str] = Field(
        default_factory=dict, description="flattened config key glob → value glob; every pair must match some key"
    )
    tags: dict[str, str] = Field(default_factory=dict, description="run tag → value glob")

    def is_empty(self) -> bool:
        return not (self.setup or self.dataset_contains or self.stage_contains or self.config or self.tags)


class Effects(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ignore_metrics: list[str] = Field(
        default_factory=list, description="metric globs set aside (canonical key or name, e.g. '*velocity*')"
    )
    ignore_classes: list[str] = Field(default_factory=list, description="classes set aside (case-insensitive)")
    caveat: str = Field("", description="shown wherever this entry applies")
    setup: str = Field("", description="setup label this entry assigns (kind: setup)")


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str = ""
    at: str = Field(default_factory=lambda: date.today().isoformat())
    evidence: str = ""
    code_refs: list[str] = Field(default_factory=list, description="file:line references in the training codebase")
    via: str = Field("", description="how it got here: ui, harness:<task>, import")


class KnowledgeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="stable id, e.g. K-manual-labels-no-velocity")
    kind: Kind = "fact"
    statement: str = Field(min_length=3)
    why: str = ""
    when: When = Field(default_factory=When)
    effects: Effects = Field(default_factory=Effects)
    source: Source = Field(default_factory=Source)
    confidence: Confidence = "medium"
    status: Status = "active"

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,80}", v):
            raise ValueError("id: letters, digits, '-', '_', '.' (2–81 chars)")
        return v
