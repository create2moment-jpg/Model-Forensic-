"""Apply the knowledge block to runs: one context for the run and one per pipeline stage (a GT finetune stage and its
pretrain stage use different data, so the same entry can apply to one and not the other)."""

from __future__ import annotations

from typing import Any

from model_forensic.analysis.config_diff import flatten
from model_forensic.knowledge import Applied, KnowledgeStore, RunContext, resolve


def _datasets(flat: dict[str, Any]) -> list[str]:
    out = []
    for k, v in flat.items():
        kl = k.lower()
        if isinstance(v, str) and ("dataset" in kl and kl.rsplit(".", 1)[-1].endswith(("name", "dataset"))):
            out.append(v)
    return out


def contexts(svc: Any, run: str) -> list[RunContext]:
    """``[run context, stage contexts…]`` from plain values (names, flattened configs, datasets, tags)."""
    r = svc.get_run(run)
    stages = [n for n, _ in svc.stages(r.ref)]
    snaps = [c for c in svc.configs(r.ref) if c.source != "params"]
    names = f"{r.name} {r.display_name or ''}"
    all_flat: dict[str, Any] = {}
    per_stage: dict[str, dict[str, Any]] = {st: {} for st in stages}
    for c in snaps:
        flat = flatten(c.data)
        all_flat.update(flat)
        head = c.source.split("/", 1)[0]
        if head in per_stage:
            per_stage[head].update(flat)
    tags = {k: str(v) for k, v in r.tags.items()}
    ds_run = [d.name for d in r.datasets] + _datasets(all_flat)
    out = [RunContext(name=names, stage="", config=all_flat, datasets=ds_run, tags=tags)]
    for st in stages:
        flat = per_stage[st] or all_flat
        out.append(
            RunContext(name=f"{names} {st}", stage=st, config=flat, datasets=_datasets(flat) or ds_run, tags=tags)
        )
    return out


def applied(svc: Any, store: KnowledgeStore, run: str) -> dict[str, Applied]:
    """Stage name ('' for the run itself) → what the knowledge changes there."""
    entries, _ = store.load()
    return {ctx.stage: resolve(entries, ctx) for ctx in contexts(svc, run)}


def combined(per_stage: dict[str, Applied]) -> Applied:
    """Union over the run and its stages (for run-level reports)."""
    out = Applied()
    seen: set[str] = set()
    for a in per_stage.values():
        for e in a.entries:
            if e.id not in seen:
                seen.add(e.id)
                out.entries.append(e)
        out.setups += [s for s in a.setups if s not in out.setups]
        out.ignore_metrics += [x for x in a.ignore_metrics if x not in out.ignore_metrics]
        out.ignore_classes += [x for x in a.ignore_classes if x not in out.ignore_classes]
        out.caveats += [x for x in a.caveats if x not in out.caveats]
    return out
