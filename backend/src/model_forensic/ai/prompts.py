"""Parameterized, versioned prompt library.

Templates are loaded from YAML packs (``ai/prompt_packs/*.yaml`` + user directories). User edits
create new versions (never overwrite), so every AI-assisted finding can record ``prompt_id@version``.
Syntax: ``{{var}}`` substitution, ``{{#var}}...{{/var}}`` (only if set) and ``{{^var}}...{{/var}}`` (only if empty).

A template can also declare the evidence it needs (``include:``), so the investigator adds e.g.
log error lines, loss-curve digests or backbone-related config values to the model's context. This
matters for local models, which often cannot call tools to fetch that data themselves.
See ``ai/prompt_packs/README.md`` for the format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from model_forensic.shared.errors import NotFoundError, ValidationError

_VAR = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_SECTION = re.compile(r"\{\{([#^])([a-z_][a-z0-9_]*)\}\}(.*?)\{\{/\2\}\}", re.S)
KNOWN_VARIABLES = {
    "run_id",
    "baseline_run_id",
    "metric_name",
    "dataset",
    "model",
    "temporal_window",
    "modalities",
    "loss_terms",
    "evaluation_split",
    "date_range",
    "hypothesis",
    # added for the study / comparison packs
    "change_description",
    "component",
    "focus_metrics",
    "slice_dimension",
    "budget_gpu_hours",
    "backbone",
    "sensor",
    "notes",
    "run_ids",
}
_LOG_MODES = {"none", "errors", "tail", "errors+tail"}


@dataclass(frozen=True)
class EvidenceSpec:
    """Extra evidence a template asks for, on top of the default context (runs, deltas, findings).

    * ``logs``: none | errors | tail | errors+tail  — error/warning lines and/or the log tail (untrusted)
    * ``series``: metric-key globs (``train/loss*``) — compact curve digests, not raw points
    * ``config_keys``: config-path globs (``*backbone*``) — values for the target (and baseline/others)
    * ``slices``: include per-slice metrics (``recall[range=60-inf]``) for single-run prompts
    * ``all_findings``: include low-severity findings too
    """

    logs: str = "none"
    series: tuple[str, ...] = ()
    config_keys: tuple[str, ...] = ()
    slices: bool = False
    all_findings: bool = False

    @classmethod
    def parse(cls, raw: dict[str, Any] | None, prompt_id: str) -> EvidenceSpec:
        raw = raw or {}
        unknown = set(raw) - {"logs", "series", "config_keys", "slices", "all_findings"}
        if unknown:
            raise ValidationError(f"prompt {prompt_id}: unknown include keys {sorted(unknown)}")
        logs = str(raw.get("logs", "none"))
        if logs not in _LOG_MODES:
            raise ValidationError(f"prompt {prompt_id}: include.logs must be one of {sorted(_LOG_MODES)}")
        return cls(
            logs=logs,
            series=tuple(raw.get("series", ())),
            config_keys=tuple(raw.get("config_keys", ())),
            slices=bool(raw.get("slices", False)),
            all_findings=bool(raw.get("all_findings", False)),
        )

    def merge(self, other: EvidenceSpec) -> EvidenceSpec:
        order = ["none", "errors", "tail", "errors+tail"]
        logs = self.logs if order.index(self.logs) >= order.index(other.logs) else other.logs
        if {self.logs, other.logs} == {"errors", "tail"}:
            logs = "errors+tail"
        return EvidenceSpec(
            logs=logs,
            series=tuple(dict.fromkeys(self.series + other.series)),
            config_keys=tuple(dict.fromkeys(self.config_keys + other.config_keys)),
            slices=self.slices or other.slices,
            all_findings=self.all_findings or other.all_findings,
        )


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    version: int
    title: str
    template: str
    tags: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    pack: str = "core"
    category: str = "general"
    description: str = ""
    include: EvidenceSpec = field(default_factory=EvidenceSpec)
    needs_baseline: bool = False  # derived: template is a comparison

    @property
    def variables(self) -> set[str]:
        return set(_VAR.findall(self.template)) | {m.group(2) for m in _SECTION.finditer(self.template)}

    def render(self, variables: dict[str, Any]) -> str:
        missing = [v for v in self.requires if not variables.get(v)]
        if missing:
            raise ValidationError(f"prompt {self.id} requires {missing}")

        def section(m: re.Match[str]) -> str:
            # {{#var}}…{{/var}} renders when var is set; {{^var}}…{{/var}} when it is empty.
            present = bool(variables.get(m.group(2)))
            return m.group(3) if present == (m.group(1) == "#") else ""

        text = _SECTION.sub(section, self.template)

        def sub(m: re.Match[str]) -> str:
            v = variables.get(m.group(1))
            return "(unspecified)" if v in (None, "") else str(v)

        return _VAR.sub(sub, text).strip()


@dataclass
class PromptLibrary:
    _prompts: dict[str, list[PromptTemplate]] = field(default_factory=dict)

    @classmethod
    def load(cls, extra_dirs: list[Path] | None = None) -> PromptLibrary:
        lib = cls()
        for f in sorted((Path(__file__).parent / "prompt_packs").glob("*.yaml")):
            lib.load_file(f)
        for d in extra_dirs or []:
            for f in sorted(d.glob("*.y*ml")):
                lib.load_file(f)
        return lib

    def load_file(self, path: Path) -> None:
        doc = yaml.safe_load(path.read_text()) or {}
        pack = doc.get("pack", path.stem)
        default_cat = doc.get("category", "general")
        default_include = EvidenceSpec.parse(doc.get("include"), f"{pack} (pack default)")
        for p in doc.get("prompts", []):
            requires = tuple(p.get("requires", []))
            self.add(
                PromptTemplate(
                    id=p["id"],
                    version=int(p.get("version", 1)),
                    title=p.get("title", p["id"]),
                    template=p["template"],
                    tags=tuple(p.get("tags", [])),
                    requires=requires,
                    pack=pack,
                    category=p.get("category", default_cat),
                    description=p.get("description", ""),
                    include=default_include.merge(EvidenceSpec.parse(p.get("include"), p["id"])),
                    needs_baseline="baseline_run_id" in requires,
                )
            )

    def add(self, t: PromptTemplate) -> None:
        unknown = t.variables - KNOWN_VARIABLES
        if unknown:
            raise ValidationError(f"prompt {t.id} uses unknown variables {sorted(unknown)}")
        versions = self._prompts.setdefault(t.id, [])
        if any(v.version == t.version for v in versions):
            raise ValidationError(f"prompt {t.id}@{t.version} already exists; edits must bump the version")
        versions.append(t)
        versions.sort(key=lambda x: x.version)

    def new_version(self, prompt_id: str, template: str, title: str | None = None) -> PromptTemplate:
        cur = self.get(prompt_id)
        t = PromptTemplate(
            id=prompt_id,
            version=cur.version + 1,
            title=title or cur.title,
            template=template,
            tags=cur.tags,
            requires=cur.requires,
            pack="user",
            category=cur.category,
            description=cur.description,
            include=cur.include,
            needs_baseline=cur.needs_baseline,
        )
        self.add(t)
        return t

    def get(self, prompt_id: str, version: int | None = None) -> PromptTemplate:
        versions = self._prompts.get(prompt_id)
        if not versions:
            raise NotFoundError(f"prompt {prompt_id!r} not found")
        if version is None:
            return versions[-1]
        for v in versions:
            if v.version == version:
                return v
        raise NotFoundError(f"prompt {prompt_id}@{version} not found")

    def all(self, tag: str | None = None) -> list[PromptTemplate]:
        latest = [v[-1] for v in self._prompts.values()]
        return sorted([p for p in latest if tag is None or tag in p.tags], key=lambda p: p.id)

    def export(self) -> str:
        return yaml.safe_dump(
            {
                "pack": "export",
                "prompts": [
                    {
                        "id": p.id,
                        "version": p.version,
                        "title": p.title,
                        "tags": list(p.tags),
                        "requires": list(p.requires),
                        "category": p.category,
                        "description": p.description,
                        "template": p.template,
                    }
                    for vs in self._prompts.values()
                    for p in vs
                ],
            },
            sort_keys=False,
        )
