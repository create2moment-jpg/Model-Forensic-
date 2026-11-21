"""Per-sequence evaluation tables (sparse-detection parquets): schema check, per-class summary, worst sequences.

Files are untrusted downloads: they are size-capped, read with pyarrow inside a guard, and classified by their
*structure* — the ``(sequence_hash, class_name)`` index plus the kind's required columns — never by file name.
Recognised kinds (the per-sequence writers of xflow sparse-detection evaluation):

* ``detection``: ``AP``, ``AP_per_distance_*``, ``ATE``…``AAE``, ``num_gt/num_tp/num_fp/num_fn``
* ``tracking``: ``assa_alpha``, ``idf1``, ``mota``
* ``combined``: ``combined_metric``, ``detection_score``, ``tracking_score``
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

MAX_BYTES = 50_000_000
INDEX = ("sequence_hash", "class_name")
HEADLINE = {"detection": "AP", "tracking": "mota", "combined": "combined_metric"}
METRICS = {
    "detection": (
        "AP",
        "AP_per_distance_0_5",
        "AP_per_distance_1_0",
        "AP_per_distance_2_0",
        "AP_per_distance_4_0",
        "ATE",
        "ASE",
        "AOE",
        "AVE",
        "AAE",
    ),
    "tracking": ("assa_alpha", "idf1", "mota"),
    "combined": ("average_precision", "attribute_score", "detection_score", "tracking_score", "combined_metric"),
}
COUNTS = ("num_gt", "num_pred", "num_tp", "num_fp", "num_fn", "num_frames")
LOWER_IS_BETTER = {"ATE", "ASE", "AOE", "AVE", "AAE"}


@dataclass
class SequenceTable:
    path: str  # remote path (or local path for files given directly)
    stage: str | None
    kind: str  # detection | tracking | combined | unknown
    note: str = ""
    columns: dict[str, np.ndarray] = field(default_factory=dict)
    seq: np.ndarray = field(default_factory=lambda: np.array([], dtype=object))
    cls: np.ndarray = field(default_factory=lambda: np.array([], dtype=object))

    @property
    def recognized(self) -> bool:
        return self.kind != "unknown"

    @property
    def label(self) -> str:
        return f"{self.stage}/{Path(self.path).name}" if self.stage else Path(self.path).name

    def classes(self) -> list[str]:
        return sorted({str(c) for c in self.cls})

    def sequences(self) -> list[str]:
        return sorted({str(s) for s in self.seq})

    def metric(self, name: str) -> np.ndarray:
        return self.columns.get(name, np.full(self.seq.size, math.nan))


def classify(columns: Sequence[str], index: Sequence[str]) -> str:
    if tuple(index) != INDEX:
        return "unknown"
    cols = set(columns)
    if {"combined_metric", "detection_score", "tracking_score"} <= cols:
        return "combined"
    if {"assa_alpha", "idf1", "mota"} <= cols:
        return "tracking"
    if "AP" in cols and {"num_gt", "num_tp"} <= cols:
        return "detection"
    return "unknown"


def load(
    path: Path, *, remote_path: str | None = None, stage: str | None = None, max_bytes: int = MAX_BYTES
) -> SequenceTable:
    """Never raises: unreadable, oversized or foreign files come back with ``kind='unknown'`` and a note."""
    name = remote_path or str(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return SequenceTable(name, stage, "unknown", note=f"cannot stat: {type(exc).__name__}")
    if size > max_bytes:
        return SequenceTable(
            name, stage, "unknown", note=f"skipped: {size:,} bytes exceeds the {max_bytes:,}-byte limit"
        )
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return SequenceTable(name, stage, "unknown", note="pyarrow not installed (pip install 'model-forensic[eval]')")
    try:
        table = pq.read_table(path)
    except Exception as exc:  # parquet readers raise many types on untrusted files
        return SequenceTable(name, stage, "unknown", note=f"not readable as parquet: {type(exc).__name__}")
    import json

    meta = table.schema.metadata or {}
    index: list[str] = []
    if b"pandas" in meta:
        try:
            index = [c for c in json.loads(meta[b"pandas"]).get("index_columns", []) if isinstance(c, str)]
        except (ValueError, TypeError):
            index = []
    names = list(table.column_names)
    if not index and set(INDEX) <= set(names):  # written without the pandas index
        index = list(INDEX)
    data_cols = [c for c in names if c not in index]
    kind = classify(data_cols, index)
    if kind == "unknown":
        return SequenceTable(
            name,
            stage,
            "unknown",
            note="unrecognised schema (expected a (sequence_hash, class_name) index with detection/"
            "tracking/combined columns); raw file left as is",
        )
    cols: dict[str, np.ndarray] = {}
    for c in data_cols:
        try:
            cols[c] = np.asarray(table.column(c).to_numpy(zero_copy_only=False), dtype=float)
        except (TypeError, ValueError):
            continue
    return SequenceTable(
        name,
        stage,
        kind,
        note=f"{kind} table: {table.num_rows} rows",
        columns=cols,
        seq=np.asarray(table.column("sequence_hash").to_pylist(), dtype=object),
        cls=np.asarray(table.column("class_name").to_pylist(), dtype=object),
    )


def _nanmean(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    return float(x.mean()) if x.size else math.nan


def per_class(t: SequenceTable) -> list[dict[str, Any]]:
    """Mean/min/max per metric, summed counts, and precision / recall / FP per GT from the counts."""
    rows = []
    for c in t.classes():
        m = t.cls == c
        row: dict[str, Any] = {"class": c, "sequences": int(m.sum())}
        for name in METRICS.get(t.kind, ()):
            if name in t.columns:
                v = t.columns[name][m]
                v = v[np.isfinite(v)]
                row[name] = float(v.mean()) if v.size else None
                row[f"{name}_min"] = float(v.min()) if v.size else None
        for name in COUNTS:
            if name in t.columns:
                row[name] = int(np.nansum(t.columns[name][m]))
        if {"num_tp", "num_fp", "num_gt"} <= set(t.columns):
            tp, fp, gt = row["num_tp"], row["num_fp"], row["num_gt"]
            row["precision"] = tp / (tp + fp) if tp + fp else None
            row["recall"] = tp / gt if gt else None
            row["fp_per_gt"] = fp / gt if gt else None
        rows.append(row)
    return rows


def worst(t: SequenceTable, metric: str | None = None, limit: int = 20, cls: str | None = None) -> list[dict[str, Any]]:
    """Worst (sequence, class) rows by a metric — lowest first, or highest for error metrics."""
    name = metric or HEADLINE.get(t.kind, "AP")
    if name not in t.columns:
        return []
    v = t.columns[name]
    m = np.isfinite(v) & ((t.cls == cls) if cls else True)
    idx = np.where(m)[0]
    order = idx[np.argsort(-v[idx] if name in LOWER_IS_BETTER else v[idx], kind="stable")][:limit]
    out = []
    for i in order:
        row: dict[str, Any] = {"sequence": str(t.seq[i]), "class": str(t.cls[i]), name: float(v[i])}
        for c in ("num_gt", "num_tp", "num_fp", "num_fn", "num_frames"):
            if c in t.columns and np.isfinite(t.columns[c][i]):
                row[c] = int(t.columns[c][i])
        out.append(row)
    return out


def summary(t: SequenceTable, worst_n: int = 15) -> dict[str, Any]:
    if not t.recognized:
        return {"path": t.path, "stage": t.stage, "kind": t.kind, "recognized": False, "note": t.note}
    head = HEADLINE[t.kind]
    return {
        "path": t.path,
        "stage": t.stage,
        "kind": t.kind,
        "recognized": True,
        "note": t.note,
        "rows": int(t.seq.size),
        "sequences": len(t.sequences()),
        "classes": t.classes(),
        "headline": {n: _nanmean(t.columns[n]) for n in METRICS[t.kind] if n in t.columns},
        "metric": head,
        "metrics": [n for n in METRICS[t.kind] if n in t.columns],
        "per_class": per_class(t),
        "worst": worst(t, head, worst_n),
    }
