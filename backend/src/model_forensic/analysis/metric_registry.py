"""Extensible metric registry: direction, unit and family per metric name.

Lookup matches exact names first, then glob patterns, so ``loss/*`` covers every component.
Teams extend it with a YAML file (``metrics:`` list of descriptor dicts).
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

from model_forensic.domain.metrics import MetricDescriptor

_BUILTIN: list[MetricDescriptor] = [
    # Detection (nuScenes-style and generic)
    MetricDescriptor(name="mAP", higher_is_better=True, family="detection"),
    MetricDescriptor(name="NDS", higher_is_better=True, family="detection"),
    MetricDescriptor(name="recall", higher_is_better=True, family="detection"),
    MetricDescriptor(name="precision", higher_is_better=True, family="detection"),
    MetricDescriptor(name="mATE", higher_is_better=False, unit="m", family="detection"),
    MetricDescriptor(name="mASE", higher_is_better=False, family="detection"),
    MetricDescriptor(name="mAOE", higher_is_better=False, unit="rad", family="detection"),
    MetricDescriptor(name="mAVE", higher_is_better=False, unit="m/s", family="detection"),
    MetricDescriptor(name="mAAE", higher_is_better=False, family="detection"),
    MetricDescriptor(name="AP", higher_is_better=True, family="detection"),
    # Tracking
    MetricDescriptor(name="AMOTA", higher_is_better=True, family="tracking"),
    MetricDescriptor(name="AMOTP", higher_is_better=False, unit="m", family="tracking"),
    MetricDescriptor(name="MOTA", higher_is_better=True, family="tracking"),
    MetricDescriptor(name="IDS", higher_is_better=False, family="tracking", min_meaningful_rel_change=0.05),
    MetricDescriptor(name="id_switches", higher_is_better=False, family="tracking", min_meaningful_rel_change=0.05),
    # Calibration / uncertainty
    MetricDescriptor(name="ECE", higher_is_better=False, family="calibration"),
    MetricDescriptor(name="NLL", higher_is_better=False, family="calibration"),
    # Losses
    MetricDescriptor(name="loss", higher_is_better=False, family="loss"),
    MetricDescriptor(name="loss/*", higher_is_better=False, family="loss"),
    MetricDescriptor(name="*_loss", higher_is_better=False, family="loss"),
    # sparse-detection evaluation (see domain/naming.py)
    MetricDescriptor(name="score", higher_is_better=True, family="detection", description="per-class/range headline"),
    MetricDescriptor(
        name="pr_auc", higher_is_better=True, family="detection", description="PR-curve AUC at recall/dist"
    ),
    MetricDescriptor(name="wp_*", higher_is_better=True, family="detection", description="working-point metric"),
    MetricDescriptor(name="match_*", higher_is_better=False, family="detection", description="3D match error"),
    MetricDescriptor(name="mean_f1_score", higher_is_better=True, family="detection"),
    MetricDescriptor(name="f1_score", higher_is_better=True, family="detection"),
    MetricDescriptor(name="idf1", higher_is_better=True, family="tracking"),
    MetricDescriptor(name="mota", higher_is_better=True, family="tracking"),
    MetricDescriptor(name="assa_alpha", higher_is_better=True, family="tracking"),
    # Multi-task losses with learned uncertainty weighting
    MetricDescriptor(name="uloss", higher_is_better=False, family="loss", description="unweighted total loss"),
    MetricDescriptor(name="uloss/*", higher_is_better=False, family="loss"),
    MetricDescriptor(name="loss_noreg", higher_is_better=False, family="loss"),
    MetricDescriptor(name="loss_noreg/*", higher_is_better=False, family="loss"),
    MetricDescriptor(name="loss_raw/*", higher_is_better=False, family="loss"),
    MetricDescriptor(
        name="task_log_sigma",
        higher_is_better=None,
        family="loss",
        description="learned log sigma; effective weight exp(-2 log sigma)",
    ),
    # Sparse detection diagnostics
    MetricDescriptor(name="positive_query_ratio", higher_is_better=None, family="sparse"),
    MetricDescriptor(name="duplicate_rate", higher_is_better=False, family="sparse"),
    # System
    MetricDescriptor(name="lr", higher_is_better=None, family="optimizer"),
    MetricDescriptor(name="grad_norm", higher_is_better=None, family="optimizer"),
    MetricDescriptor(name="gpu_mem_gb", higher_is_better=False, unit="GB", family="system"),
    MetricDescriptor(name="throughput", higher_is_better=True, unit="samples/s", family="system"),
]


class MetricRegistry:
    def __init__(self, descriptors: list[MetricDescriptor] | None = None) -> None:
        self._exact: dict[str, MetricDescriptor] = {}
        self._patterns: list[MetricDescriptor] = []
        for d in descriptors if descriptors is not None else _BUILTIN:
            self.register(d)

    def register(self, d: MetricDescriptor) -> None:
        if any(ch in d.name for ch in "*?["):
            self._patterns.append(d)
        else:
            self._exact[d.name] = d
            self._exact.setdefault(d.name.lower(), d)

    def lookup(self, name: str) -> MetricDescriptor:
        if name in self._exact:
            return self._exact[name]
        if name.lower() in self._exact:
            return self._exact[name.lower()]
        for d in self._patterns:
            if fnmatch.fnmatchcase(name, d.name):
                return d
        # Strip a leading namespace ("eval/NDS" -> "NDS").
        if "/" in name:
            tail = name.rsplit("/", 1)[1]
            found = self.lookup(tail)
            if found.name != tail or found.higher_is_better is not None or found.family != "generic":
                return found
        return MetricDescriptor(name=name)

    def load_yaml(self, path: Path) -> None:
        doc = yaml.safe_load(path.read_text()) or {}
        for item in doc.get("metrics", []):
            self.register(MetricDescriptor(**item))


default_registry = MetricRegistry()
