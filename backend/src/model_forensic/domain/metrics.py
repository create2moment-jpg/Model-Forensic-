"""Typed metric model.

A metric is identified by a :class:`MetricKey`: a name, a split and an ordered set of
*dimensions* (e.g. ``range=60-inf``, ``class=pedestrian``, ``modality=lidar``). Dimensions are
what make localization ("degrades only beyond 60 m") possible without per-metric code.
"""

from __future__ import annotations

import math
import re
from enum import Enum

import numpy as np
from pydantic import Field, ValidationInfo, field_validator

from model_forensic.domain.base import DomainModel


class Split(str, Enum):
    TRAIN = "train"
    VAL = "val"
    TEST = "test"
    UNKNOWN = "unknown"


class Aggregation(str, Enum):
    LAST = "last"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    SUM = "sum"


class StepAxis(str, Enum):
    STEP = "step"
    EPOCH = "epoch"
    TIME = "time"


_DIM_RE = re.compile(r"^(?P<name>[^\[]+?)(?:\[(?P<dims>[^\]]*)\])?$")
# MLflow (and so Azure ML) only allows [A-Za-z0-9_./ :-] in metric names, so slice dimensions are
# encoded as ``name__key_value__key_value`` when logged from training code (see model_forensic_instrument).
# Dimension keys are lowercase letters only, which makes the encoding unambiguous.
_MLFLOW_DIM_RE = re.compile(r"^[a-z]+_.+$")


class MetricKey(DomainModel):
    name: str
    split: Split = Split.UNKNOWN
    dims: tuple[tuple[str, str], ...] = ()

    @field_validator("dims")
    @classmethod
    def _sorted(cls, v: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(v))

    @property
    def dim_dict(self) -> dict[str, str]:
        return dict(self.dims)

    @property
    def base(self) -> MetricKey:
        return MetricKey(name=self.name, split=self.split)

    def __str__(self) -> str:
        s = f"{self.split.value}/{self.name}" if self.split != Split.UNKNOWN else self.name
        if self.dims:
            s += "[" + ",".join(f"{k}={v}" for k, v in self.dims) + "]"
        return s

    def to_mlflow(self) -> str:
        """MLflow-safe name (inverse of the ``__key_value`` branch of :meth:`parse`)."""
        s = f"{self.split.value}/{self.name}" if self.split != Split.UNKNOWN else self.name
        return s + "".join(f"__{k}_{v}" for k, v in self.dims)

    @classmethod
    def parse(cls, text: str) -> MetricKey:
        """Parse ``val/recall[range=60-inf,class=car]`` or the MLflow-safe ``val/recall__range_60-inf__class_car``."""
        split = Split.UNKNOWN
        head, _, rest = text.partition("/")
        if rest and head in {s.value for s in Split}:
            split, text = Split(head), rest
        text = text.strip()
        if "[" not in text and "__" in text:
            name, *parts = text.split("__")
            if name and parts and all(_MLFLOW_DIM_RE.match(p) for p in parts):
                enc = tuple((k, v) for k, _, v in (p.partition("_") for p in parts))
                return cls(name=name, split=split, dims=enc)
        m = _DIM_RE.match(text)
        if not m:
            raise ValueError(f"invalid metric key: {text!r}")
        dims: list[tuple[str, str]] = []
        if m.group("dims"):
            for part in m.group("dims").split(","):
                k, _, v = part.partition("=")
                dims.append((k.strip(), v.strip()))
        return cls(name=m.group("name").strip(), split=split, dims=tuple(dims))


class MetricDescriptor(DomainModel):
    """Registry entry describing how to interpret a metric family."""

    name: str
    higher_is_better: bool | None = None
    unit: str | None = None
    family: str = "generic"  # loss | detection | tracking | calibration | system | generic
    description: str | None = None
    # Relative change considered meaningful when no replicate variance is available.
    min_meaningful_rel_change: float = 0.01


class MetricSeries(DomainModel):
    """A sampled metric. Arrays are stored as tuples to keep the model immutable/hashable."""

    key: MetricKey
    steps: tuple[float, ...]
    values: tuple[float, ...]
    axis: StepAxis = StepAxis.STEP
    timestamps: tuple[float, ...] | None = None
    unit: str | None = None
    source: str = "unknown"  # e.g. "mlflow", "metrics.jsonl"
    provenance: dict[str, str] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def _len(cls, v: tuple[float, ...], info: ValidationInfo) -> tuple[float, ...]:
        steps = info.data.get("steps")
        if steps is not None and len(steps) != len(v):
            raise ValueError("steps and values must have equal length")
        return v

    def __len__(self) -> int:
        return len(self.values)

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return np.asarray(self.steps, dtype=float), np.asarray(self.values, dtype=float)

    def finite(self) -> tuple[np.ndarray, np.ndarray]:
        s, v = self.arrays()
        mask = np.isfinite(v)
        return s[mask], v[mask]

    def aggregate(self, how: Aggregation = Aggregation.LAST) -> float:
        _, v = self.finite()
        if v.size == 0:
            return math.nan
        if how == Aggregation.LAST:
            return float(v[-1])
        if how == Aggregation.MEAN:
            return float(v.mean())
        if how == Aggregation.MIN:
            return float(v.min())
        if how == Aggregation.MAX:
            return float(v.max())
        return float(v.sum())

    @property
    def has_nonfinite(self) -> bool:
        return any(not math.isfinite(x) for x in self.values)
