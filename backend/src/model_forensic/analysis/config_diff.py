"""Semantic configuration diff.

Configs are flattened to dotted paths, compared structurally (not textually), classified into
forensic categories by configurable rules, and runtime noise (output dirs, seeds-as-ids,
timestamps) is separated from meaningful changes.
"""

from __future__ import annotations

import fnmatch
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from model_forensic.domain.config import ChangeKind, ConfigChange, ConfigDiff, ConfigurationSnapshot
from model_forensic.domain.run import RunRef

DEFAULT_CATEGORY_RULES: list[tuple[str, str]] = [
    # (glob on lower-cased dotted path, category) — first match wins.
    ("*seed*", "randomness"),
    ("*optim*", "optimization"),
    ("lr", "optimization"),
    ("*.lr", "optimization"),
    ("*_lr", "optimization"),
    ("*lr_*", "optimization"),
    ("*learning_rate*", "optimization"),
    ("*weight_decay*", "optimization"),
    ("*warmup*", "optimization"),
    ("*batch_size*", "optimization"),
    ("*grad*clip*", "optimization"),
    ("*epoch*", "optimization"),
    ("*max_steps*", "optimization"),
    ("*sched*", "optimization"),
    ("*loss*", "loss"),
    ("*uncertain*", "uncertainty"),
    ("*temporal*", "temporal"),
    ("*num_frames*", "temporal"),
    ("*frames*", "temporal"),
    ("*memory*", "temporal"),
    ("*queue*", "temporal"),
    ("*sequence*", "temporal"),
    ("*modalit*", "modality"),
    ("*camera*", "modality"),
    ("*lidar*", "modality"),
    ("*radar*", "modality"),
    ("*fusion*", "modality"),
    ("*assign*", "assignment"),
    ("*matcher*", "assignment"),
    ("*num_queries*", "architecture"),
    ("*query*", "architecture"),
    ("*decoder*", "architecture"),
    ("*encoder*", "architecture"),
    ("*backbone*", "architecture"),
    ("*model.*", "architecture"),
    ("*augment*", "data"),
    ("*data*", "data"),
    ("*class*", "data"),
    ("*range*", "data"),
    ("*eval*", "evaluation"),
    ("*threshold*", "evaluation"),
    ("*nms*", "evaluation"),
]

DEFAULT_NOISE_PATTERNS: list[str] = [
    "*output_dir*",
    "*out_dir*",
    "*log_dir*",
    "*run_id*",
    "*run_name*",
    "*timestamp*",
    "*experiment_name*",
    "*checkpoint_dir*",
    "*work_dir*",
    "*job_name*",
    "*azureml*",
]


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested mappings; lists of scalars stay as values, lists of mappings are indexed."""
    out: dict[str, Any] = {}
    if isinstance(obj, Mapping):
        if not obj and prefix:
            out[prefix] = {}
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, Sequence) and not isinstance(obj, (str, bytes)) and any(isinstance(x, Mapping) for x in obj):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = list(obj) if isinstance(obj, tuple) else obj
    return out


def _coerce(v: Any) -> Any:
    """Treat '1e-4' and 0.0001 as equal: configs round-trip through strings in many backends."""
    if isinstance(v, str):
        s = v.strip()
        low = s.lower()
        if low in {"true", "false"}:
            return low == "true"
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            return s
    return v


def _equal(a: Any, b: Any) -> bool:
    a, b = _coerce(a), _coerce(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    return bool(a == b)


@dataclass
class ConfigDiffer:
    category_rules: list[tuple[str, str]] = field(default_factory=lambda: list(DEFAULT_CATEGORY_RULES))
    noise_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_NOISE_PATTERNS))

    def categorize(self, path: str) -> str:
        p = path.lower()
        for pattern, cat in self.category_rules:
            if fnmatch.fnmatchcase(p, pattern):
                return cat
        return "other"

    def is_noise(self, path: str) -> bool:
        p = path.lower()
        return any(fnmatch.fnmatchcase(p, pat) for pat in self.noise_patterns)

    def merge(self, snapshots: Sequence[ConfigurationSnapshot]) -> dict[str, Any]:
        """Merge all config sources of a run into one flat namespace, prefixed by source."""
        merged: dict[str, Any] = {}
        for snap in snapshots:
            prefix = "" if snap.source in {"config", "config.yaml", "config.json"} else f"{snap.source}."
            for k, v in flatten(snap.data).items():
                merged[prefix + k] = v
        return merged

    def diff(
        self,
        baseline: RunRef,
        base_cfg: Sequence[ConfigurationSnapshot],
        candidate: RunRef,
        cand_cfg: Sequence[ConfigurationSnapshot],
    ) -> ConfigDiff:
        a, b = self.merge(base_cfg), self.merge(cand_cfg)
        changes: list[ConfigChange] = []
        for path in sorted(set(a) | set(b)):
            in_a, in_b = path in a, path in b
            if in_a and in_b and _equal(a[path], b[path]):
                continue
            kind = ChangeKind.CHANGED if in_a and in_b else (ChangeKind.ADDED if in_b else ChangeKind.REMOVED)
            rel: float | None = None
            if kind == ChangeKind.CHANGED:
                ca, cb = _coerce(a[path]), _coerce(b[path])
                numeric = isinstance(ca, (int, float)) and isinstance(cb, (int, float)) and not isinstance(ca, bool)
                if numeric and ca != 0 and self.categorize(path) != "randomness":
                    rel = (float(cb) - float(ca)) / abs(float(ca))
            changes.append(
                ConfigChange(
                    path=path,
                    kind=kind,
                    baseline=a.get(path),
                    candidate=b.get(path),
                    category=self.categorize(path),
                    relative_change=rel,
                    is_noise=self.is_noise(path),
                )
            )
        return ConfigDiff(baseline=baseline, candidate=candidate, changes=tuple(changes))
