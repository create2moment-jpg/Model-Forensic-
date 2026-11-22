"""Knowledge entries (schema mirrored in ``harness/knowledge.schema.json``)."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Kind = Literal["constraint", "fact", "setup", "caveat", "code_ref", "belief"]
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


class BeliefTest(BaseModel):
    """How a belief (knowledge from experience) is checked against runs. ``config_effect``: changing a config key
    moves a metric; ``loss_behaviour``: a loss family behaves a certain way. ``scope`` is a run search query."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["config_effect", "loss_behaviour"] = "config_effect"
    key: str = Field("", description="config_effect: flattened config key, suffix match (e.g. optimizer.lr)")
    change: Literal["increase", "decrease", "set"] = "increase"
    value: str = Field("", description="config_effect with change=set: the value the key is set to")
    metric: str = Field("", description="config_effect: metric key as logged (e.g. val/score[class=combined_metric])")
    expect: Literal["up", "down", "no_change"] = "up"
    min_effect: float = Field(0.01, ge=0, le=1, description="relative change that counts as an effect")
    family: str = Field("", description="loss_behaviour: loss family (radar, camera, decoder_bbox…) or term glob")
    behaviour: Literal["muted", "trusted", "plateaus_early", "still_learning", "rising"] = "muted"
    scope: str = Field("", description="run search query the belief is about; empty = the newest runs")
    max_runs: int = Field(40, ge=2, le=200)


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
    test: BeliefTest | None = Field(None, description="kind belief: how to check it against runs")

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,80}", v):
            raise ValueError("id: letters, digits, '-', '_', '.' (2–81 chars)")
        return v
