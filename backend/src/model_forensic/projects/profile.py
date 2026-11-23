"""Project profiles: how one target project maps onto Model Forensic's fixed interfaces — configuration, not code.

A profile (``config/projects/<id>.local.yaml``, git-ignored; ``*.example.yaml`` are committed templates) says which
workspaces / run folders belong to the project, which experiments, how its metric names map to canonical keys, what
its pipeline stages are (setup / training / evaluation / …), where its code diff and evaluation files are, and its own
terminology and knowledge folders. The core never branches on a project: it reads these values through
:class:`ProjectProfile`. ``contract_version`` says which version of this contract a profile was written for.

Each workspace belongs to at most one project (explicit identity, no silent sharing).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from model_forensic.domain.naming import NamingRules
from model_forensic.shared import yamlsafe

CONTRACT_VERSION = 1
StageRole = Literal["setup", "data", "training", "evaluation", "export", "analysis", "other"]
DEFAULT_STAGE_ROLES: tuple[tuple[str, StageRole], ...] = (  # first match wins: evaluate_pretrain is an evaluation
    ("*setup*", "setup"),
    ("*eval*", "evaluation"),
    ("*test*", "evaluation"),
    ("*bench*", "evaluation"),
    ("*prepare*", "data"),
    ("*data*", "data"),
    ("*train*", "training"),
    ("*finetune*", "training"),
    ("*export*", "export"),
    ("*analy*", "analysis"),
)


class MetricsMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aliases: dict[str, str] = Field(default_factory=dict, description="exact raw metric name → name the core knows")
    rules: list[dict[str, str]] = Field(
        default_factory=list, description='[{match: regex, to: replacement}] applied in order, e.g. "\\\\1" groups'
    )
    headline: str = Field("", description="the metric that summarises a run for this project (e.g. val/f1)")

    @field_validator("rules")
    @classmethod
    def _rules(cls, v: list[dict[str, str]]) -> list[dict[str, str]]:
        for r in v:
            if set(r) != {"match", "to"}:
                raise ValueError("each rule needs exactly 'match' and 'to'")
            try:
                re.compile(r["match"])
            except re.error as exc:  # not a ValueError: report it, never crash the load
                raise ValueError(f"invalid regex {r['match']!r}: {exc}") from None
        return v

    def naming(self) -> NamingRules:
        return NamingRules(tuple(self.aliases.items()), tuple((r["match"], r["to"]) for r in self.rules))


class ArtifactLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code_diff: list[str] = Field(
        default_factory=lambda: ["code/*repo_diff*.txt", "*/code/*repo_diff*.txt", "*repo_diff*.txt"],
        description="globs of the code diff the training job saved (relative to the run's files)",
    )
    evaluation: list[str] = Field(default_factory=list, description="extra globs of evaluation outputs")
    configs: list[str] = Field(default_factory=list, description="extra globs of composed / resolved configs")


class LocalRoot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    root: str = Field(description="folder in Model Forensic's local layout; relative → under PROJECT_PATH")


class ProjectProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str = ""
    description: str = ""
    contract_version: int = CONTRACT_VERSION
    workspaces: list[str] = Field(default_factory=list, description="workspace names (config/azure.local.yaml)")
    local_roots: list[LocalRoot] = Field(default_factory=list, description="local run folders of this project")
    experiments: list[str] = Field(default_factory=list, description="experiment name globs; empty = all")
    metrics: MetricsMapping = Field(default_factory=MetricsMapping)
    stages: dict[str, StageRole] = Field(default_factory=dict, description="stage name glob → role")
    artifacts: ArtifactLayout = Field(default_factory=ArtifactLayout)
    terminology: dict[str, str] = Field(default_factory=dict, description="core term → this project's word")
    knowledge_sources: list[str] = Field(default_factory=list, description="documentation folders of this project")

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", v):
            raise ValueError("id: lower-case letters, digits, '-', '_' (1–41 chars)")
        return v

    @property
    def backends(self) -> list[str]:
        return [*self.workspaces, *(r.name for r in self.local_roots)]

    def stage_role(self, stage: str) -> StageRole:
        s = stage.lower()
        for pat, role in self.stages.items():
            if fnmatch.fnmatchcase(s, pat.lower()):
                return role
        return stage_role_default(stage)

    def owns_experiment(self, experiment: str | None) -> bool:
        return not self.experiments or any(fnmatch.fnmatchcase(experiment or "", p) for p in self.experiments)

    def term(self, word: str) -> str:
        return self.terminology.get(word, word)


def stage_role_default(stage: str) -> StageRole:
    s = stage.lower()
    for pat, role in DEFAULT_STAGE_ROLES:
        if fnmatch.fnmatchcase(s, pat):
            return role
    return "other"


def load_profiles(folder: Path) -> tuple[list[ProjectProfile], list[str]]:
    """All ``*.local.yaml`` profiles in ``folder`` (examples are templates, not loaded) and readable problems."""
    out: list[ProjectProfile] = []
    notes: list[str] = []
    if not folder.is_dir():
        return out, notes
    owner: dict[str, str] = {}
    for f in sorted(folder.glob("*.local.yaml")):
        p, err = parse_profile(f.read_text(encoding="utf-8"), f.name)
        if err:
            notes.append(err)
            continue
        assert p is not None
        if any(x.id == p.id for x in out):
            notes.append(f"{f.name}: project id {p.id!r} is used twice — skipped")
            continue
        clash = [b for b in p.backends if b in owner]
        if clash:
            notes.append(
                f"{f.name}: workspace(s) {', '.join(clash)} already belong to project {owner[clash[0]]!r} — "
                "a workspace belongs to one project; skipped"
            )
            continue
        for b in p.backends:
            owner[b] = p.id
        out.append(p)
    return out, notes


def parse_profile(text: str, name: str = "profile") -> tuple[ProjectProfile | None, str | None]:
    try:
        doc: Any = yamlsafe.load(text)
    except (ValueError, yaml.YAMLError) as exc:
        return None, f"{name}: not valid YAML ({str(exc)[:200]})"
    if not isinstance(doc, dict):
        return None, f"{name}: expected a mapping"
    v = doc.get("contract_version", CONTRACT_VERSION)
    if isinstance(v, int) and v > CONTRACT_VERSION:
        return None, (
            f"{name}: written for contract version {v}, this Model Forensic supports {CONTRACT_VERSION} — "
            "upgrade Model Forensic (see docs/projects.md → Versions)"
        )
    try:
        return ProjectProfile.model_validate(doc), None
    except ValidationError as exc:
        msgs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        return None, f"{name}: {msgs}"
