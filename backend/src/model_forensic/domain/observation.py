"""Observation: the one shape every analysis result takes for its consumers (design review D4).

The health questions, loss drift, loss dynamics (LR schedule vs learning), the expected-vs-actual checks, model internals and the stored findings each have
their own detail; the Expert review, the dossier, the ledger and reports read only this — so a new analysis needs one
producer, not one mapping per consumer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["health", "drift", "dynamics", "code", "internals", "finding", "comparison"]
Status = Literal["bad", "warn", "ok", "na", "info"]
Cause = Literal["data", "implementation", "config", "schedule", "optimisation", "unknown"]

RANK: dict[str, int] = {"bad": 0, "warn": 1, "info": 2, "ok": 3, "na": 4}


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(description="stable within a run and stage: '<source>:<what>'")
    source: Source
    status: Status
    title: str = Field(description="the question, check or finding it answers")
    statement: str = Field(description="what was found, with the numbers")
    evidence: tuple[str, ...] = Field(default=(), description="metric keys that show it (to plot)")
    cause: Cause = "unknown"
    next_step: str = ""
    why: str = ""
    look: str = Field("", description="where to look in the UI")
    code_refs: tuple[str, ...] = ()
    measure: float | None = None
    param: str = ""
    stage: str | None = None

    @property
    def is_problem(self) -> bool:
        return self.status in ("bad", "warn")
