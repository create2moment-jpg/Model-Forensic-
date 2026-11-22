"""Which entries apply to a run (or one stage of it), and what they change."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from model_forensic.knowledge.model import KnowledgeEntry


@dataclass
class RunContext:
    """Plain values the conditions are evaluated against — nothing backend-specific."""

    name: str = ""  # run / stage name and display name
    stage: str = ""
    config: Mapping[str, Any] = field(default_factory=dict)  # flattened
    datasets: Sequence[str] = ()
    tags: Mapping[str, str] = field(default_factory=dict)
    setups: list[str] = field(default_factory=list)  # filled by setup entries


@dataclass
class Applied:
    entries: list[KnowledgeEntry] = field(default_factory=list)
    setups: list[str] = field(default_factory=list)
    ignore_metrics: list[tuple[str, str]] = field(default_factory=list)  # (glob, entry id)
    ignore_classes: list[tuple[str, str]] = field(default_factory=list)  # (class, entry id)
    caveats: list[tuple[str, str]] = field(default_factory=list)  # (text, entry id)

    def metric_reason(self, key: str) -> str | None:
        """The entry id that sets this metric aside (canonical key or bare name), if any."""
        low = key.lower()
        name = low.split("[", 1)[0].rsplit("/", 1)[-1]
        for glob, eid in self.ignore_metrics:
            g = glob.lower()
            if fnmatch.fnmatchcase(low, g) or fnmatch.fnmatchcase(name, g):
                return eid
        return None

    def class_reason(self, cls: str) -> str | None:
        return next((eid for c, eid in self.ignore_classes if c.lower() == cls.lower()), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.model_dump() for e in self.entries],
            "setups": self.setups,
            "ignore_metrics": [{"glob": g, "entry": e} for g, e in self.ignore_metrics],
            "ignore_classes": [{"class": c, "entry": e} for c, e in self.ignore_classes],
            "caveats": [{"text": t, "entry": e} for t, e in self.caveats],
        }


def _any_sub(needles: Iterable[str], hay: Iterable[str]) -> bool:
    hs = [h.lower() for h in hay if h]
    return any(n.lower() in h for n in needles for h in hs)


def matches(e: KnowledgeEntry, ctx: RunContext) -> bool:
    w = e.when
    if w.setup and not {s.lower() for s in w.setup} & {s.lower() for s in ctx.setups}:
        return False
    if w.dataset_contains and not _any_sub(w.dataset_contains, ctx.datasets):
        return False
    # a stage is judged by its own name; the run itself (no stage) by its run / display name
    if w.stage_contains and not _any_sub(w.stage_contains, [ctx.stage] if ctx.stage else [ctx.name]):
        return False
    for kg, vg in w.config.items():
        hit = any(
            fnmatch.fnmatchcase(str(k).lower(), kg.lower()) and fnmatch.fnmatchcase(str(v).lower(), str(vg).lower())
            for k, v in ctx.config.items()
        )
        if not hit:
            return False
    for tk, tv in w.tags.items():
        v = ctx.tags.get(tk)
        if v is None or not fnmatch.fnmatchcase(str(v).lower(), str(tv).lower()):
            return False
    return True


def resolve(entries: Sequence[KnowledgeEntry], ctx: RunContext) -> Applied:
    """Active entries only. Setup entries are resolved first so other entries can say ``when: {setup: [...]}``."""
    out = Applied()
    active = [e for e in entries if e.status == "active"]
    for e in active:
        if e.kind == "setup" and e.effects.setup and matches(e, ctx) and e.effects.setup not in ctx.setups:
            ctx.setups.append(e.effects.setup)
            out.setups.append(e.effects.setup)
            out.entries.append(e)
    for e in active:
        if e.kind == "setup" or not matches(e, ctx):
            continue
        out.entries.append(e)
        out.ignore_metrics += [(g, e.id) for g in e.effects.ignore_metrics]
        out.ignore_classes += [(c, e.id) for c in e.effects.ignore_classes]
        if e.effects.caveat:
            out.caveats.append((e.effects.caveat, e.id))
    return out
