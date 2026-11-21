"""Per-class evaluation JSONs of sparse-detection evaluation (the ``eval_json`` download preset).

Layout (one folder per class, one sub-folder per artifact family)::

    …/evaluation/<labels>/<Class>/torch_metric_evaluation/torch_metric_evaluation.json   # ruler-free scoreboard
    …/<Class>/pr_curves_detailed/pr_curves_detailed__measure=distance.json             # {"auc": {recall: {dist: v}}}
    …/<Class>/false_positives_analysis/*.json                                            # fppf + worst_sequences
    …/<Class>/bb3d_match_evaluation/*.json                                               # *_binned_by_* bins with size
    …/<Class>/working_point/*.json
    …/combined_metrics_results*.json                                                     # headline (ruler-specific)

Files are untrusted: size-capped, parsed in a guard, recognised by folder + content shape. Nothing is inferred
from a value's name alone — e.g. ``AP_per_distance_X`` is AP at a TP *center tolerance* of X m, not ego range.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

MAX_BYTES = 20_000_000
FAMILIES = (
    "torch_metric_evaluation",
    "pr_curves_detailed",
    "false_positives_analysis",
    "bb3d_match_evaluation",
    "working_point",
    "attribute_confusion_matrix",
)
GLOSSARY = {
    "AP_per_distance_*": "AP with a true-positive center tolerance of X m (0.5/1/2/4) — not an ego-range bucket",
    "ATE/ASE/AOE/AVE/AAE": "translation / scale / orientation / velocity / attribute errors of matched boxes",
    "AVE": "velocity error; degenerate when ground-truth velocity is zero (manual labels) — check GT coverage",
    "pr_auc[dist]": "AUC at a recall working point per ego-distance bucket (bucket edges differ per ruler)",
    "combined_metric": "headline of one combined-metrics ruler — comparable only between runs sharing that ruler",
}
_BIN = re.compile(r"binned_by_(?P<attr>[a-z_]+)$")


@dataclass
class ClassEval:
    metrics: dict[str, float] = field(default_factory=dict)  # torch_metric_evaluation (finite values only)
    auc_by_distance: dict[str, dict[float, float]] = field(default_factory=dict)  # recall → {distance: auc}
    fppf: dict[str, float] = field(default_factory=dict)  # threshold → FP per frame
    worst_fp: list[tuple[str, float]] = field(default_factory=list)  # (sequence, avg FP per frame), worst first
    gt_bins: dict[str, list[int]] = field(default_factory=dict)  # attribute → GT count per bin
    working_point: dict[str, float] = field(default_factory=dict)


@dataclass
class EvalEvidence:
    stage: str | None
    root: str  # common prefix of the files (for provenance)
    classes: dict[str, ClassEval] = field(default_factory=dict)
    combined: dict[str, float] = field(default_factory=dict)
    files: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.classes and not self.combined


def _finite(d: Any) -> dict[str, float]:
    return {
        str(k): float(v)
        for k, v in (d or {}).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    }


def _float(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _walk_bins(node: Any, out: dict[str, list[int]], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            m = _BIN.search(str(k))
            if m:
                bins = v.values() if isinstance(v, dict) else v if isinstance(v, list) else []
                sizes = [
                    int(b["size"]) for b in bins if isinstance(b, dict) and isinstance(b.get("size"), (int, float))
                ]
                if sizes and m.group("attr") not in out:
                    out[m.group("attr")] = sizes
            else:
                _walk_bins(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node[:50]:
            _walk_bins(v, out, depth + 1)


def parse_file(rel_path: str, doc: Any, ev: EvalEvidence) -> bool:
    """Fold one parsed JSON into ``ev``. Returns whether it was recognised."""
    parts = PurePosixPath(rel_path).parts
    name = parts[-1]
    if name.startswith("combined_metrics_results") and isinstance(doc, dict):
        flat: dict[str, float] = {}

        def walk(prefix: str, node: Any, depth: int = 0) -> None:
            if depth > 4 or len(flat) > 400:
                return
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(f"{prefix}.{k}" if prefix else str(k), v, depth + 1)
            elif (f := _float(node)) is not None and not isinstance(node, bool):
                flat[prefix] = f

        walk("", doc)
        ev.combined.update(flat)
        return True
    fam = next((p for p in parts[:-1] if p in FAMILIES), None)
    if fam is None or not isinstance(doc, dict):
        return False
    i = parts.index(fam)
    if i == 0:
        return False
    ce = ev.classes.setdefault(parts[i - 1], ClassEval())
    if fam == "torch_metric_evaluation":
        ce.metrics.update(_finite(doc))
    elif fam == "pr_curves_detailed" and "distance" in name and isinstance(doc.get("auc"), dict):
        for thr, by in doc["auc"].items():
            if isinstance(by, dict):
                pts = {d: v for k, x in by.items() if (d := _float(k)) is not None and (v := _float(x)) is not None}
                if pts:
                    ce.auc_by_distance[f"{float(thr):g}" if _float(thr) is not None else str(thr)] = pts
    elif fam == "false_positives_analysis":
        ce.fppf.update(
            {f"{float(k):g}" if _float(k) is not None else str(k): v for k, v in _finite(doc.get("fppf")).items()}
        )
        worst = []
        for w in doc.get("worst_sequences") or []:
            if isinstance(w, (list, tuple)) and len(w) == 2 and isinstance(w[1], dict):
                v = _float(w[1].get("average_false_positives"))
                if v is not None:
                    worst.append((str(w[0]), v))
        if worst:
            ce.worst_fp = sorted(worst, key=lambda t: -t[1])
    elif fam == "bb3d_match_evaluation":
        _walk_bins(doc, ce.gt_bins)
    elif fam == "working_point":
        ce.working_point.update(_finite(doc))
    return True


def load(files: list[tuple[str, Path]], stage: str | None = None, max_bytes: int = MAX_BYTES) -> EvalEvidence:
    """``files``: (remote path, local path) pairs of one evaluation (one run or one stage)."""
    rels = [r for r, _ in files]
    root = rels[0].rsplit("/", 1)[0] if len(rels) == 1 else _common(rels)
    ev = EvalEvidence(stage=stage, root=root)
    for rel, local in files:
        try:
            if local.stat().st_size > max_bytes:
                ev.notes.append(f"skipped {PurePosixPath(rel).name}: larger than {max_bytes:,} bytes")
                continue
            doc = json.loads(local.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            ev.notes.append(f"unreadable {PurePosixPath(rel).name}: {type(exc).__name__}")
            continue
        if parse_file(rel, doc, ev):
            ev.files += 1
    return ev


def _common(paths: list[str]) -> str:
    split = [p.split("/") for p in paths]
    out = []
    for parts in zip(*split, strict=False):
        if len(set(parts)) != 1:
            break
        out.append(parts[0])
    return "/".join(out)


def gt_degenerate(ce: ClassEval, share: float = 0.95) -> dict[str, float]:
    """Attributes whose ground truth sits ≥ ``share`` in one bin (metrics on them measure the prediction only)."""
    out = {}
    for attr, sizes in ce.gt_bins.items():
        total = sum(sizes)
        if total > 0 and max(sizes) / total >= share:
            out[attr] = max(sizes) / total
    return out
