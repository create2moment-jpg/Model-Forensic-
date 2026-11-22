"""Harness adaptation: a brief for a coding agent inside the training project, and the knowledge schema.

The brief is everything Model Forensic observed for a run — stages, datasets, config keys and values, loss and metric
names as logged, the knowledge it already applies — plus a task from ``harness/tasks``. The agent answers with
knowledge YAML (``harness/knowledge.schema.json``) that ``mf knowledge import`` stores as *proposed*.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from model_forensic.analysis.config_diff import flatten
from model_forensic.knowledge import KnowledgeEntry, KnowledgeStore

_SKIP_KEY_PARTS = ("output_dir", "root_dir", "log_dir", "cache", "tmp", "password", "token", "secret", "sas", "key_")


def harness_dir() -> Path:
    from model_forensic.shared.paths import home_path

    env = os.environ.get("MF_HARNESS_DIR")
    if env:
        return home_path(env)
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "harness" / "tasks").is_dir():
            return p / "harness"
    return home_path("harness")


def tasks() -> dict[str, str]:
    d = harness_dir() / "tasks"
    if not d.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.md")) if not p.stem.startswith("_")}


def schema() -> dict[str, Any]:
    s = KnowledgeEntry.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Model Forensic knowledge file",
        "type": "object",
        "required": ["entries"],
        "properties": {"entries": {"type": "array", "items": {"$ref": "#/$defs/KnowledgeEntry"}}},
        "$defs": {**s.pop("$defs", {}), "KnowledgeEntry": s},
    }


def schema_text() -> str:
    return json.dumps(schema(), indent=2) + "\n"


def _short(v: Any, n: int = 80) -> str:
    t = json.dumps(v, default=str) if not isinstance(v, str) else v
    return t if len(t) <= n else t[: n - 1] + "…"


_STOP = {"and", "the", "of", "a", "to", "profiles", "definitions"}


def related_docs(docindex: Any, words: list[str], limit: int = 8) -> list[Any]:
    """Documentation sections that mention any of ``words`` (best first, one hit per section)."""
    seen: dict[tuple[str, str, str], Any] = {}
    for w in words:
        for h in docindex.search(w, limit=limit):
            key = (h.source, h.path, h.anchor)
            if key not in seen or h.score > seen[key].score:
                seen[key] = h
    return sorted(seen.values(), key=lambda h: -h.score)[:limit]


def brief(
    svc: Any,
    store: KnowledgeStore,
    run: str,
    task: str | None = None,
    max_config: int = 500,
    docindex: Any = None,
) -> str:
    from model_forensic.application.knowledge import applied, combined

    r = svc.get_run(run)
    stages = svc.stages(r.ref)
    lines = [
        f"# Model Forensic brief — {r.display_name or r.name}",
        "",
        "You are a coding agent inside the training codebase this run came from. Use this brief as the ground truth of"
        " what the run logged; read the code to answer the task at the end. Answer only with knowledge YAML.",
        "",
        "## Run",
        f"- kind: {r.kind.value}; status: {r.status.value}; experiment: {r.experiment or '—'}",
        f"- stages (pipeline order): {', '.join(n for n, _ in stages) or '— (single job)'}",
    ]
    try:
        kn = combined(applied(svc, store, run))
        if kn.setups:
            lines.append(f"- setup labels already known: {', '.join(kn.setups)}")
    except Exception:  # the brief must not fail on knowledge problems
        kn = None
    # Config: flattened keys with values, per source, secrets and paths skipped.
    lines += ["", "## Configuration (flattened keys as Model Forensic matches them)", ""]
    n = 0
    for snap in svc.configs(r.ref):
        if snap.source == "params":
            continue
        lines.append(f"### {snap.source}")
        for k, v in sorted(flatten(snap.data).items()):
            if any(p in k.lower() for p in _SKIP_KEY_PARTS):
                continue
            lines.append(f"- `{k}` = `{_short(v)}`")
            n += 1
            if n >= max_config:
                lines.append(f"- … truncated after {max_config} keys")
                break
        if n >= max_config:
            break
    # Logged names (raw names are what the code writes).
    series = svc.series(r.ref)
    raw = sorted({s.provenance.get("raw_name") or str(s.key) for s in series})
    losses = [x for x in raw if "loss" in x.lower() or "sigma" in x.lower()]
    metrics = [x for x in raw if x not in losses]
    lines += ["", f"## Loss and weighting series as logged ({len(losses)})", ""]
    lines += [f"- `{x}`" for x in losses[:400]]
    lines += ["", f"## Other metrics as logged ({len(metrics)})", ""]
    lines += [f"- `{x}`" for x in metrics[:400]]
    datasets = sorted(
        {
            str(v)
            for k, v in flatten({c.source: c.data for c in svc.configs(r.ref)}).items()
            if "dataset" in k.lower() and k.lower().endswith(("name", "dataset")) and isinstance(v, str)
        }
    )
    if datasets:
        lines += ["", "## Dataset names", ""] + [f"- `{d}`" for d in datasets]
    entries, _ = store.load()
    if entries:
        lines += ["", "## Knowledge Model Forensic already has (do not duplicate; refine or contradict with refs)", ""]
        lines += [f"- {e.id} ({e.kind}, {e.status}): {e.statement}" for e in entries[:100]]
    if kn is not None and kn.entries:
        lines += ["", "Applied to this run: " + ", ".join(e.id for e in kn.entries)]
    if docindex is not None and task:
        words = [w for w in task.split("_") if w not in _STOP]
        hits = related_docs(docindex, words)
        if hits:
            lines += ["", "## Documentation already written (read it first; refine instead of repeating it)", ""]
            lines += [f"- {h.source}:{h.path}{'#' + h.anchor if h.anchor else ''} — {h.heading}" for h in hits]
    lines += [
        "",
        "## Where to write",
        "",
        f"- Knowledge YAML: `{store.folder}/{(task or 'knowledge').replace('_', '-')}-<yyyy-mm-dd>.yaml`"
        " (entries with `status: proposed`)",
        f"- Longer explanations: `{store.folder}/docs/<codebase>/<topic>.md` (Markdown, one topic per file)",
        "- Nowhere else: Model Forensic reads this inbox; the user reviews proposed entries before they apply.",
    ]
    if task:
        all_tasks = tasks()
        if task not in all_tasks:
            raise ValueError(
                f"unknown task {task!r}; available: {', '.join(all_tasks) or 'none (harness/tasks missing)'}"
            )
        rules = harness_dir() / "tasks" / "_rules.md"
        lines += [
            "",
            "---",
            "",
            all_tasks[task].strip(),
            "",
            rules.read_text(encoding="utf-8").strip() if rules.is_file() else "",
        ]
    return "\n".join(lines).rstrip() + "\n"
