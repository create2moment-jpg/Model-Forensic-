"""Team policy: de-prioritised classes, expected metric families, metric → code references, analysis defaults.

Loaded from ``config/team.local.yaml`` (git-ignored; template ``config/team.example.yaml``) or ``MF_TEAM_CONFIG``.
A missing or broken file yields the empty policy plus a notice — it never stops the app.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_PATH = Path("config/team.local.yaml")


class OverviewPolicy(BaseModel):
    """What a run's Overview shows first: the combined metrics, then one metric per class."""

    combined: list[str] = Field(
        default_factory=lambda: ["*combined*"],
        description="glob patterns (on the metric key) of the combined / headline metrics, shown first, in order",
    )
    per_class_metric: str = Field(
        "",
        description="the metric shown once per class: a glob on the key without its class dimension (e.g. "
        "'val/mean_f1_score*'), or a template with {class} when the class is part of the name (e.g. "
        "'val/{class}[task=sparse_detection]'); empty = the evaluation metric with the most classes",
    )
    class_dim: str = "class"
    exclude_classes: list[str] = Field(default_factory=list, description="besides deprioritized_classes")
    max_classes: int = 12


class TeamPolicy(BaseModel):
    deprioritized_classes: list[str] = Field(default_factory=list)
    expected_metrics: dict[str, dict[str, str]] = Field(default_factory=dict)  # stage kind → {pattern: label}
    code_refs: dict[str, str] = Field(default_factory=dict)  # metric glob → code location
    analysis_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    overview: OverviewPolicy = Field(default_factory=OverviewPolicy)
    source: str | None = None

    @field_validator("deprioritized_classes", mode="before")
    @classmethod
    def _one_or_many(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else (v or [])

    @field_validator("expected_metrics", mode="before")
    @classmethod
    def _lists_ok(cls, v: Any) -> Any:
        """Accept ``kind: [pattern, ...]`` as well as ``kind: {pattern: label}``."""
        if not isinstance(v, dict):
            return {}
        return {k: ({p: p for p in pats} if isinstance(pats, list) else dict(pats or {})) for k, pats in v.items()}

    def is_deprioritized(self, cls: str) -> bool:
        return cls.lower() in {c.lower() for c in self.deprioritized_classes}

    def code_ref(self, metric: str) -> str | None:
        name = metric.split("[", 1)[0]
        for pat, ref in self.code_refs.items():
            if fnmatch.fnmatchcase(name, pat) or fnmatch.fnmatchcase(metric, pat):
                return ref
        return None


def path() -> Path:
    from model_forensic.shared.paths import home_path

    return home_path(os.environ.get("MF_TEAM_CONFIG", str(DEFAULT_PATH)))


def load(p: Path | None = None) -> tuple[TeamPolicy, str | None]:
    """``(policy, notice)``; the notice explains a file that could not be used."""
    f = p or path()
    if not f.exists():
        return TeamPolicy(), None
    try:
        doc = yaml.safe_load(f.read_text()) or {}
        pol = TeamPolicy.model_validate({k: v for k, v in doc.items() if k != "source"})
    except Exception as exc:
        return TeamPolicy(), f"Team policy {f}: {type(exc).__name__}: {exc}"
    pol.source = str(f)
    return pol, None
