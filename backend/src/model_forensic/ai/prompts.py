"""Parameterized, versioned prompt library.

Templates are loaded from YAML packs (built-in + user directories). User edits create new
versions (never overwrite), so every AI-assisted finding can record ``prompt_id@version``.
Syntax: ``{{var}}`` substitution and ``{{#var}}...{{/var}}`` optional sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from model_forensic.shared.errors import NotFoundError, ValidationError

_VAR = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_SECTION = re.compile(r"\{\{#([a-z_][a-z0-9_]*)\}\}(.*?)\{\{/\1\}\}", re.S)
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
}


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    version: int
    title: str
    template: str
    tags: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    pack: str = "core"

    @property
    def variables(self) -> set[str]:
        return set(_VAR.findall(self.template)) | {m.group(1) for m in _SECTION.finditer(self.template)}

    def render(self, variables: dict[str, Any]) -> str:
        missing = [v for v in self.requires if not variables.get(v)]
        if missing:
            raise ValidationError(f"prompt {self.id} requires {missing}")

        def section(m: re.Match[str]) -> str:
            return m.group(2) if variables.get(m.group(1)) else ""

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
        lib.load_file(Path(__file__).parent / "prompt_packs" / "core.yaml")
        for d in extra_dirs or []:
            for f in sorted(d.glob("*.y*ml")):
                lib.load_file(f)
        return lib

    def load_file(self, path: Path) -> None:
        doc = yaml.safe_load(path.read_text()) or {}
        pack = doc.get("pack", path.stem)
        for p in doc.get("prompts", []):
            self.add(
                PromptTemplate(
                    id=p["id"],
                    version=int(p.get("version", 1)),
                    title=p.get("title", p["id"]),
                    template=p["template"],
                    tags=tuple(p.get("tags", [])),
                    requires=tuple(p.get("requires", [])),
                    pack=pack,
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
                        "template": p.template,
                    }
                    for vs in self._prompts.values()
                    for p in vs
                ],
            },
            sort_keys=False,
        )
