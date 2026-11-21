"""Offline inspection of downloaded artefacts (``mf inspect <folder>``): what Model Forensic recognises, what not.

Works on any folder — the v1 tool's ``outputs/_azure_artefacts`` tree, a Model Forensic download, or a single run
folder — without Azure access. Every file type the analyses read is checked with the same readers they use:

* metric names (``metric_histories.json`` of the v1 tool, ``metrics.jsonl``, Lightning ``metrics.csv`` headers)
  → canonical keys; names that do not map to a split are listed;
* ``composed_config.yaml`` → parses (Python tags included), ruler and checkpoint monitor found;
* per-class evaluation JSONs → recognised families per class, unrecognised JSONs listed;
* per-sequence parquets → recognised schema or the reason why not.

Nothing is modified; unreadable files become notes, never crashes.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from model_forensic.analysis import evaljson, sequences
from model_forensic.analysis.config_diff import flatten
from model_forensic.analysis.study_checks import config_values, ruler_identity
from model_forensic.domain.metrics import Split
from model_forensic.infrastructure.connectors.naming import canonical_key
from model_forensic.shared import yamlsafe

MAX_FILES = 20_000
MAX_JSON_BYTES = 50_000_000


@dataclass
class InspectReport:
    root: str
    files: int = 0
    metric_names: int = 0
    mapped: int = 0
    unmapped: list[str] = field(default_factory=list)
    mapped_examples: dict[str, str] = field(default_factory=dict)
    configs: list[dict[str, Any]] = field(default_factory=list)
    eval_classes: dict[str, list[str]] = field(default_factory=dict)  # class → families recognised
    eval_unrecognised: list[str] = field(default_factory=list)
    gt_degenerate: dict[str, dict[str, float]] = field(default_factory=dict)
    parquets: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.notes and (not self.metric_names or len(self.unmapped) <= self.metric_names * 0.05)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__ | {"ok": self.ok()}


def _metric_names(path: Path) -> list[str]:
    if path.name == "metric_histories.json":
        doc = json.loads(path.read_text(errors="replace"))
        names: list[str] = []
        for st in (doc.get("stages") or {}).values():
            names += list((st or {}).get("histories", {}) or {})
        names += list(doc.get("histories", {}) or {})
        return names
    if path.name == "metrics.jsonl":
        out = []
        with path.open(errors="replace") as fh:
            for line in fh:
                try:
                    out.append(str(json.loads(line)["key"]))
                except (ValueError, KeyError, TypeError):
                    continue
        return out
    if path.name == "metrics.csv":
        head = path.open(errors="replace").readline()
        cols = next(csv.reader(io.StringIO(head)), [])
        return [c for c in cols if c not in {"step", "epoch", "", "time"}]
    return []


def inspect(root: Path) -> InspectReport:
    rep = InspectReport(root=str(root))
    names: Counter[str] = Counter()
    eval_ev = evaljson.EvalEvidence(stage=None, root=str(root))
    families: dict[str, set[str]] = {}
    for i, f in enumerate(sorted(p for p in root.rglob("*") if p.is_file())):
        if i >= MAX_FILES:
            rep.notes.append(f"stopped after {MAX_FILES} files")
            break
        rep.files += 1
        rel = f.relative_to(root).as_posix()
        try:
            if f.name in {"metric_histories.json", "metrics.jsonl", "metrics.csv"}:
                names.update(_metric_names(f))
            elif f.name == "composed_config.yaml" or (f.suffix in {".yaml", ".yml"} and "config" in f.name):
                doc = yamlsafe.load(f.read_text(errors="replace"))
                flat = flatten(doc) if isinstance(doc, dict) else {}
                rulers = [v for v in config_values(flat, "combined_metrics_yaml").values() if isinstance(v, str)]
                mons = {k: v for k, v in flat.items() if isinstance(v, str) and k.endswith("checkpoint_monitor")}
                rep.configs.append(
                    {
                        "path": rel,
                        "keys": len(flat),
                        "ruler": ruler_identity(rulers[0]) if rulers else None,
                        "monitor": next(iter(mons.values()), None),
                    }
                )
            elif f.suffix == ".json" and f.name != "metric_histories.json":
                if f.stat().st_size > MAX_JSON_BYTES:
                    rep.notes.append(f"{rel}: skipped (> {MAX_JSON_BYTES:,} bytes)")
                    continue
                before = {
                    c: (len(v.metrics), len(v.auc_by_distance), len(v.worst_fp), len(v.gt_bins))
                    for c, v in eval_ev.classes.items()
                }
                if evaljson.parse_file(rel, json.loads(f.read_text(errors="replace")), eval_ev):
                    fam = next((p for p in rel.split("/") if p in evaljson.FAMILIES), "combined_metrics_results")
                    parts = rel.split("/")
                    cls = parts[parts.index(fam) - 1] if fam in parts and parts.index(fam) > 0 else "(run)"
                    families.setdefault(cls, set()).add(fam)
                    after = eval_ev.classes.get(cls)
                    if (
                        after is not None
                        and before.get(cls, (0, 0, 0, 0))
                        == (len(after.metrics), len(after.auc_by_distance), len(after.worst_fp), len(after.gt_bins))
                        and fam in {"torch_metric_evaluation", "pr_curves_detailed", "bb3d_match_evaluation"}
                    ):
                        rep.notes.append(f"{rel}: recognised as {fam} but no values extracted (format drift?)")
                elif "evaluation" in rel:
                    rep.eval_unrecognised.append(rel)
            elif f.suffix == ".parquet":
                t = sequences.load(f, remote_path=rel)
                rep.parquets.append({"path": rel, "kind": t.kind, "note": t.note})
        except Exception as exc:  # untrusted files: report, never crash
            rep.notes.append(f"{rel}: {type(exc).__name__}: {str(exc)[:160]}")
    rep.metric_names = len(names)
    for n in names:
        k = canonical_key(n)
        if k.split == Split.UNKNOWN:
            rep.unmapped.append(n)
        else:
            rep.mapped += 1
            if len(rep.mapped_examples) < 25:
                rep.mapped_examples[n] = str(k)
    rep.unmapped.sort()
    rep.eval_classes = {c: sorted(v) for c, v in sorted(families.items())}
    rep.gt_degenerate = {c: d for c, ce in eval_ev.classes.items() if (d := evaljson.gt_degenerate(ce))}
    rep.eval_unrecognised = rep.eval_unrecognised[:200]
    return rep
