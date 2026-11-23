"""Plot specifications — a plot as validated data (schema v1).

Every plot the application draws — rule-based suggestions, an analysis section (uncertainty weighting), a plot the
local LLM proposes, or one a person writes — is a :class:`PlotSpecV1`. The schema is strict (unknown fields are
refused), bounded (at most 12 series) and *grounded*: :func:`application.plotting.validate` additionally checks that
every run and metric key exists and that the requested scale fits the data. A spec that fails is never drawn; the
errors say why. The JSON Schema (``PlotSpecV1.model_json_schema()``) is what the plotter prompt gives the LLM.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Transform = Literal["raw", "ema", "exp_neg2", "normalize", "delta_from_first"]


class Axis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field("", max_length=80)
    scale: Literal["linear", "log"] = "linear"


class SeriesRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: str = Field(..., min_length=1, max_length=300, description="run id as <workspace>:<run id>")
    key: str = Field(..., min_length=1, max_length=300, description="canonical metric key, e.g. val/loss")
    label: str = Field("", max_length=120)
    transform: Transform = Field(
        "raw",
        description="raw · ema (smoothed) · exp_neg2 (effective weight exp(-2x) of a learned log σ) · normalize "
        "(divide by the first finite value) · delta_from_first (subtract it)",
    )
    ema_alpha: float = Field(0.1, gt=0, le=1)
    stage: str = Field(
        "", max_length=120,
        description="pipeline stage whose copy of the key to plot (empty: the run's headline, its last stage)",
    )  # fmt: skip


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: float
    label: str = Field("", max_length=80)


class PlotSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    id: str = Field(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(..., min_length=1, max_length=120)
    question: str = Field("", max_length=300, description="what this plot answers")
    kind: Literal["line", "scatter"] = "line"
    x: Axis = Field(default_factory=lambda: Axis(label="step"))
    y: Axis = Field(default_factory=Axis)
    series: list[SeriesRef] = Field(..., min_length=1, max_length=12)
    annotations: list[Annotation] = Field(default_factory=list, max_length=20)
    source: Literal["rules", "analysis", "ai", "user"] = "user"
