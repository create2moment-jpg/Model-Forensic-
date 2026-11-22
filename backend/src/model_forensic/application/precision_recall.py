"""Precision & recall deep dive (ported from the v1 tool's rules).

* **Working points** — explicitly logged precision / recall / F1 per stage × class × *target* recall. The target recall
  in the metric name and the *measured* recall value stay separate; the shortfall is target − measured.
* **PR-curve AUC** per class × distance × target recall.
* **Count check** — TP / FP / FN / GT summed from the per-sequence parquets and the precision / recall those counts
  imply (a different threshold than a working point; shown side by side, not merged).
* **Log lines** — precision / recall / F1 / calibration / ECE / Brier / reliability lines in every log of the run and
  its stages.
* **Evaluation files** — every file under ``evaluation/``, ``summaries/``, ``visualizations/`` with what it can and
  cannot show.

Deliberately not done: no smooth curve is drawn through a few operating points, and low precision / recall alone is
never reported as undertraining or miscalibration — calibration needs confidence-vs-outcome evidence (ECE, a
reliability curve).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from model_forensic.application.run_logs import scan_logs

PR_LOG_RE = re.compile(
    r"\b(?:precision|recall|f1(?:[_ -]?score)?|calibration|confidence|reliability|ece|brier|"
    r"pr[-_ ]?curves?|average[_ -]?precision)\b",
    re.IGNORECASE,
)
WP_METRICS = {"wp_precision": "precision", "wp_recall": "recall", "wp_f1_score": "f1"}
CAVEATS = [
    "Operating points are shown as logged — no curve is interpolated between them.",
    "Target recall (in the metric name) and measured recall (the logged value) are different numbers.",
    "Low precision or recall alone does not prove undertraining or miscalibration; calibration needs ECE or a "
    "reliability curve.",
]

GUIDES: dict[str, tuple[str, str]] = {
    "working point": (
        "Precision, measured recall and F1 at the selected confidence threshold.",
        "A single operating point does not show the full threshold trade-off.",
    ),
    "pr curve": (
        "The precision/recall trade-off across score thresholds and whether a useful operating region exists.",
        "The curve alone does not explain why errors occur or prove calibration quality.",
    ),
    "distance-stratified PR": (
        "Where precision/recall performance changes across distance buckets.",
        "Distance-specific degradation is a clue for investigation, not a causal diagnosis.",
    ),
    "false-positive analysis": (
        "Recurring false-positive patterns and possible label or scene-related issues.",
        "Interpret categories together with examples, sample counts and the matching threshold.",
    ),
    "attribute confusion matrix": (
        "Which labels or attributes are confused with one another.",
        "Check axes, normalisation and support counts before comparing cells.",
    ),
    "3D match evaluation": (
        "Localisation quality of matched boxes: centre, size, orientation and velocity errors.",
        "Errors only cover matched boxes; a constant ground-truth attribute makes its error meaningless (P3).",
    ),
    "per-sequence table": (
        "Metrics and TP/FP/FN/GT counts per recording — find the scenes that drive an average.",
        "Counts depend on the matching threshold; compare runs only with the same evaluation config.",
    ),
    "scalar metrics": (
        "Aggregate evaluation metrics and supporting counts or metadata.",
        "Confirm the columns and evaluation configuration before comparing runs.",
    ),
    "evaluation plots": (
        "Visual diagnostics for examples or patterns not summarised by scalar metrics.",
        "Supporting evidence; inspect selectively rather than treating every plot as a finding.",
    ),
    "other evaluation output": (
        "An evaluation artifact the guide does not recognise.",
        "Not interpreted automatically; verify its schema and provenance.",
    ),
}
_EVAL_DIRS = ("evaluation/", "summaries/", "visualizations/", "visualization/", "plots/")
_IMAGE = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")


def guide(path: str) -> dict[str, Any]:
    p = path.casefold()
    name = PurePosixPath(p).name
    if "pr_curves_detailed" in p:
        fam = "distance-stratified PR"
    elif "pr_curves" in p:
        fam = "pr curve"
    elif "working_point" in p:
        fam = "working point"
    elif "false_positives_analysis" in p:
        fam = "false-positive analysis"
    elif "attribute_confusion_matrix" in p:
        fam = "attribute confusion matrix"
    elif "bb3d_match_evaluation" in p:
        fam = "3D match evaluation"
    elif name.endswith(".parquet"):
        fam = "per-sequence table"
    elif "torch_metric_evaluation" in p or name in {"metrics.csv", "combined_metrics_results.json"}:
        fam = "scalar metrics"
    elif "/plots/" in p or "/visualizations/" in p or name.endswith(_IMAGE):
        fam = "evaluation plots"
    else:
        fam = "other evaluation output"
    parts = PurePosixPath(path).parts
    low = [x.casefold() for x in parts]
    cls = None
    if "alliance_manual_labels" in low:
        i = low.index("alliance_manual_labels") + 1
        if i < len(parts) - 1 and low[i] not in {"pr_curves", "plots"}:
            cls = parts[i]
    purpose, caveat = GUIDES[fam]
    return {"family": fam, "purpose": purpose, "caveat": caveat, "class": cls}


def is_eval_file(path: str) -> bool:
    p = path.casefold()
    return any(d in p for d in _EVAL_DIRS) or p.endswith(".parquet")


def ordered_classes(classes: Sequence[str], deprioritized: Sequence[str]) -> list[str]:
    dep = {c.casefold() for c in deprioritized}
    uniq = sorted(set(classes), key=str.casefold)
    return [c for c in uniq if c.casefold() not in dep] + [c for c in uniq if c.casefold() in dep]


def _last(s: Any) -> float | None:
    _, v = s.finite()
    return float(v[-1]) if v.size else None


def working_points(series: Sequence[Any]) -> list[dict[str, Any]]:
    pts: dict[tuple[str, str, float], dict[str, Any]] = {}
    for s in series:
        m = WP_METRICS.get(s.key.name)
        d = s.key.dim_dict
        if m is None or "class" not in d or "recall" not in d:
            continue
        try:
            target = float(d["recall"])
        except ValueError:
            continue
        v = _last(s)
        if v is None:
            continue
        key = (d.get("stage", ""), d["class"], target)
        pts.setdefault(key, {"stage": key[0], "class": key[1], "target_recall": target})[m] = v
    rows = sorted(pts.values(), key=lambda r: (r["stage"], r["class"], r["target_recall"]))
    for r in rows:
        if r.get("recall") is not None:
            r["shortfall"] = max(0.0, r["target_recall"] - r["recall"])
    return rows


def auc_rows(series: Sequence[Any]) -> list[dict[str, Any]]:
    rows = []
    for s in series:
        d = s.key.dim_dict
        if s.key.name != "pr_auc" or not {"class", "dist", "recall"} <= set(d):
            continue
        v = _last(s)
        try:
            dist, rec = float(d["dist"]), float(d["recall"])
        except ValueError:
            continue
        if v is not None and math.isfinite(v):
            rows.append(
                {"stage": d.get("stage", ""), "class": d["class"], "target_recall": rec, "distance_m": dist, "auc": v}
            )
    return sorted(rows, key=lambda r: (r["stage"], r["class"], r["target_recall"], r["distance_m"]))


def collect(svc: Any, run: str, knowledge: Any = None) -> dict[str, Any]:
    """Everything except the log search (which downloads logs) and the file list."""
    ref = svc.ref(run)
    series = svc.series(ref)
    pipeline = bool(svc.stages(ref))
    keep = [s for s in series if not pipeline or "stage" in s.key.dim_dict]  # pipelines: the per-stage copies
    wps, aucs = working_points(keep), auc_rows(keep)
    counts: list[dict[str, Any]] = []
    notes: list[str] = []
    try:
        from model_forensic.analysis import sequences

        for t in svc.eval_tables(ref):
            if t.kind != "detection":
                continue
            for row in sequences.per_class(t):
                if row.get("num_gt") is not None:
                    counts.append(
                        {
                            "stage": t.stage or "",
                            **{
                                k: row.get(k)
                                for k in (
                                    "class",
                                    "sequences",
                                    "num_tp",
                                    "num_fp",
                                    "num_fn",
                                    "num_gt",
                                    "precision",
                                    "recall",
                                    "fp_per_gt",
                                )
                            },
                        }
                    )
    except Exception as exc:  # parquets are optional evidence
        notes.append(f"per-sequence counts unavailable: {type(exc).__name__}")
    deprio = list(getattr(svc.team, "deprioritized_classes", []) or [])
    aside: dict[str, str] = {}
    if knowledge is not None:
        kn = knowledge(run)
        for c, eid in kn.ignore_classes:
            aside[c] = eid
            if c.casefold() not in {d.casefold() for d in deprio}:
                deprio.append(c)
        notes += [f"{t} ({e})" for t, e in kn.caveats]
    classes = ordered_classes(
        [r["class"] for r in wps] + [r["class"] for r in aucs] + [r["class"] for r in counts], deprio
    )
    if not wps and not aucs:
        notes.append(
            "no working-point or PR-curve metrics logged — download the Evaluation JSON preset; the "
            "evaluation files are listed below either way."
        )
    short = [r for r in wps if (r.get("shortfall") or 0) > 0.02]
    if short:
        worst = max(short, key=lambda r: r["shortfall"])
        notes.append(
            f"{len(short)} working point(s) miss their target recall; the largest gap is {worst['class']} "
            f"({worst['stage'] or 'run'}): target {worst['target_recall']:.2f}, measured {worst['recall']:.3f}."
        )
    return {
        "classes": classes,
        "deprioritized": [c for c in classes if c.casefold() in {d.casefold() for d in deprio}],
        "stages": [
            st for st in [n for n, _ in svc.stages(ref)] + [""] if st in {r["stage"] for r in wps + aucs + counts}
        ],
        "working_points": wps,
        "auc": aucs,
        "counts": counts,
        "notes": notes,
        "caveats": CAVEATS,
        "set_aside_classes": aside,
    }


def log_lines(svc: Any, run: str, limit: int = 200) -> dict[str, Any]:
    scan = scan_logs(svc, svc.ref(run), [PR_LOG_RE], max_hits=limit)
    return {
        "lines": [{"stage": h.stage, "path": h.path, "line_no": h.line_no, "text": h.line} for h in scan.hits],
        "files": scan.files,
        "notes": scan.notes,
    }


def eval_files(svc: Any, run: str) -> list[dict[str, Any]]:
    out = []
    for a in svc.artifacts(svc.ref(run)):
        if not is_eval_file(a.path):
            continue
        cached = svc.artifact_store.cached(a) is not None
        out.append(
            {
                "path": a.path,
                "ref": f"{a.run}::{a.path}" if a.stage else a.path,
                "stage": a.stage or "",
                "size_bytes": a.size_bytes,
                "cached": cached,
                **guide(a.path),
            }
        )
    return sorted(out, key=lambda r: (r["stage"], r["family"], r["class"] or "", r["path"]))
