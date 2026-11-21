"""Metric naming shared with the Model Forensic backend.

Canonical form: ``<split>/<name>[key=value,...]`` e.g. ``val/recall[range=60-inf]``.
MLflow / Azure ML reject ``[ = ,`` in metric names, so sinks that talk to MLflow use the encoded form
``val/recall__range_60-inf``. Dimension keys must be lowercase letters only (``range``, ``class``,
``module``, ``scale``, ``layer``, ``ablation``, ``branch``); values may contain ``_ . -`` but not ``__``.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

_KEY = re.compile(r"^[a-z]+$")
_SAFE_VALUE = re.compile(r"[^A-Za-z0-9_.\- ]")


def _clean_value(v: object) -> str:
    s = str(v).replace("__", "_")
    return _SAFE_VALUE.sub("-", s)


def metric_name(name: str, split: Optional[str] = None, **dims: object) -> str:
    """Canonical (bracket) name: ``metric_name("recall", "val", range="60-inf")`` → ``val/recall[range=60-inf]``."""
    for k in dims:
        if not _KEY.match(k):
            raise ValueError(f"dimension key {k!r} must be lowercase letters only")
    base = f"{split}/{name}" if split else name
    if not dims:
        return base
    inner = ",".join(f"{k}={_clean_value(v)}" for k, v in sorted(dims.items()))
    return f"{base}[{inner}]"


def decode(text: str) -> Tuple[str, Dict[str, str]]:
    """Split a canonical or encoded name into (base, dims)."""
    if "[" in text and text.endswith("]"):
        base, _, inner = text[:-1].partition("[")
        dims = dict(p.split("=", 1) for p in inner.split(",") if "=" in p)
        return base, dims
    if "__" in text:
        base, *parts = text.split("__")
        if parts and all(re.match(r"^[a-z]+_.+$", p) for p in parts):
            return base, dict(p.split("_", 1) for p in parts)
    return text, {}


def encode(text: str) -> str:
    """Canonical → MLflow-safe: ``val/recall[range=60-inf]`` → ``val/recall__range_60-inf``."""
    base, dims = decode(text)
    return base + "".join(f"__{k}_{_clean_value(v)}" for k, v in sorted(dims.items()))


def with_dims(text: str, **extra: object) -> str:
    """Add dimensions to an existing name (canonical form)."""
    base, dims = decode(text)
    dims.update({k: _clean_value(v) for k, v in extra.items()})
    split, _, name = (
        base.partition("/") if "/" in base and base.split("/", 1)[0] in {"train", "val", "test"} else ("", "", base)
    )
    return metric_name(name or base, split or None, **dims)
