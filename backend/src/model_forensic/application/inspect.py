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
from model_forensic.domain.messages import ParseMessage, counts, shape_of
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
    notes: list[str] = field(default_factory=list)  # the messages as text (kept for older consumers)
    messages: list[ParseMessage] = field(default_factory=list)

    def say(self, m: ParseMessage) -> None:
        self.messages.append(m)
        self.notes.append(m.text())

    def ok(self) -> bool:
        return not any(m.blocking for m in self.messages) and (
            not self.metric_names or len(self.unmapped) <= self.metric_names * 0.05
        )

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "messages"}
        return d | {
            "ok": self.ok(),
            "messages": [m.to_dict() for m in self.messages],
            "message_counts": counts(self.messages),
        }


# What each evaluation family's reader extracts (the ``expected`` side of an unsupported-schema message).
EXPECTED_EVAL: dict[str, dict[str, Any]] = {
    "torch_metric_evaluation": {"type": "object", "values": "numeric leaves (AP, ATE, … per class)"},
    "pr_curves_detailed": {
        "type": "object",
        "keys": ["auc"],
        "auc": "{threshold: {distance bucket: number}}",
        "file_name": "*distance*",
    },
    "bb3d_match_evaluation": {"type": "object", "values": "GT bins per attribute ({attribute: {bin: count}})"},
}


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
    names_files = [0]
    for i, f in enumerate(sorted(p for p in root.rglob("*") if p.is_file())):
        if i >= MAX_FILES:
            rep.say(ParseMessage("recoverable_warning", "inspect.file_limit", f"stopped after {MAX_FILES} files",
                                 remediation="inspect a sub-folder (one run or one stage)"))  # fmt: skip
            break
        rep.files += 1
        rel = f.relative_to(root).as_posix()
        try:
            if f.name in {"metric_histories.json", "metrics.jsonl", "metrics.csv"}:
                names_files[0] += 1
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
                    rep.say(
                        ParseMessage(
                            "recoverable_warning", "json.too_large", f"skipped (> {MAX_JSON_BYTES:,} bytes)", rel
                        )
                    )
                    continue
                before = {
                    c: (len(v.metrics), len(v.auc_by_distance), len(v.worst_fp), len(v.gt_bins))
                    for c, v in eval_ev.classes.items()
                }
                doc = json.loads(f.read_text(errors="replace"))
                if evaljson.parse_file(rel, doc, eval_ev):
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
                        rep.say(
                            ParseMessage(
                                "unsupported_schema",
                                "eval.no_values",
                                f"recognised as {fam} but no values extracted (format drift?)",
                                rel,
                                expected=EXPECTED_EVAL.get(fam),
                                observed=shape_of(doc),
                                remediation="compare expected and observed; adapt analysis/evaljson.py::parse_file and "
                                "add a sanitised fixture under tests/fixtures/real_layout/",
                            )
                        )
                elif "evaluation" in rel:
                    rep.eval_unrecognised.append(rel)
                    if len(rep.eval_unrecognised) <= 50:
                        rep.messages.append(
                            ParseMessage(
                                "info",
                                "eval.unrecognised",
                                "an evaluation JSON no reader knows (not used by any analysis)",
                                rel,
                                expected={"families": list(evaljson.FAMILIES)},
                                observed=shape_of(doc),
                            )
                        )
            elif f.suffix == ".parquet":
                t = sequences.load(f, remote_path=rel)
                rep.parquets.append({"path": rel, "kind": t.kind, "note": t.note})
                if t.message is not None:
                    rep.say(t.message)
        except Exception as exc:  # untrusted files: report, never crash
            rep.say(
                ParseMessage(
                    "parse_failure",
                    "file.unreadable",
                    f"{type(exc).__name__}: {str(exc)[:160]}",
                    rel,
                    remediation="re-download the file (it may be truncated); if it is valid, the reader needs a fix",
                )
            )
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
    if rep.unmapped:
        rep.messages.append(
            ParseMessage(
                "recoverable_warning",
                "naming.unmapped",
                f"{len(rep.unmapped)} of {rep.metric_names} metric name(s) do not map to a split (train / val / test)",
                expected={"pattern": "<split>/<name> or a project profile naming rule"},
                observed={"examples": rep.unmapped[:10]},
                remediation="add a naming rule in the project profile (config/projects/*.yaml → naming)",
            )
        )
    if rep.files and not rep.metric_names and not names_files[0]:
        rep.messages.append(
            ParseMessage(
                "missing_data",
                "metrics.none",
                "no metric history file (metric_histories.json, metrics.jsonl, metrics.csv) in this folder",
                remediation="download the run's metrics, or point mf inspect at the run folder",
            )
        )
    elif names_files[0] and not rep.metric_names:
        rep.say(
            ParseMessage(
                "missing_data",
                "metrics.empty",
                "metric history files are present but contain no metric names",
                observed={"files": names_files[0]},
                remediation="check the files; they may be truncated or in a new layout",
            )
        )
    rep.eval_classes = {c: sorted(v) for c, v in sorted(families.items())}
    rep.gt_degenerate = {c: d for c, ce in eval_ev.classes.items() if (d := evaljson.gt_degenerate(ce))}
    rep.eval_unrecognised = rep.eval_unrecognised[:200]
    return rep
