"""Thresholds of the deterministic analyses, in configuration (design review D5).

Defaults: ``defaults/analysis.yaml`` (value, meaning, direction of the alarm). Overrides: ``config/team.local.yaml`` →
``analysis_params.<section>.<key>`` (the container applies them at start; ``mf thresholds`` and Settings show the
result). The calibration work order proposes overrides from verdicts on real runs (``application/calibration``).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULTS = Path(__file__).resolve().parents[1] / "defaults" / "analysis.yaml"
SECTIONS = ("loss_questions", "loss_drift", "internals", "expectations")
_overrides: dict[str, dict[str, float]] = {}


@lru_cache(maxsize=1)
def defaults() -> dict[str, dict[str, dict[str, Any]]]:
    data = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8")) or {}
    return {s: dict(data.get(s) or {}) for s in SECTIONS}


def t(section: str, key: str) -> float:
    """The threshold in force (override, else default). Unknown keys are a programming error."""
    spec = defaults()[section][key]
    return float(_overrides.get(section, {}).get(key, spec["value"]))


def alarm_when(section: str, key: str) -> str:
    return str(defaults()[section][key].get("alarm_when", "above"))


def configure(params: Mapping[str, Mapping[str, Any]] | None) -> list[str]:
    """Apply overrides (``analysis_params``); sections of other analyses are left alone. Returns notices."""
    global _overrides
    notes: list[str] = []
    out: dict[str, dict[str, float]] = {}
    d = defaults()
    for section, values in (params or {}).items():
        if section not in SECTIONS:
            continue  # a plugin's parameters (handled by the plugin registry)
        for key, value in (values or {}).items():
            if key not in d[section]:
                notes.append(f"analysis_params.{section}.{key}: unknown threshold — ignored (mf thresholds lists them)")
                continue
            try:
                out.setdefault(section, {})[key] = float(value)
            except (TypeError, ValueError):
                notes.append(f"analysis_params.{section}.{key}: {value!r} is not a number — ignored")
    _overrides = out
    return notes


def table() -> list[dict[str, Any]]:
    """Every threshold: section, key, value in force, default, source, direction, meaning."""
    rows = []
    for section, keys in defaults().items():
        for key, spec in keys.items():
            ov = _overrides.get(section, {}).get(key)
            rows.append({"section": section, "key": key, "value": ov if ov is not None else float(spec["value"]),
                         "default": float(spec["value"]), "source": "team" if ov is not None else "default",
                         "alarm_when": spec.get("alarm_when", "above"), "help": spec.get("help", "")})  # fmt: skip
    return rows


@contextmanager
def override(params: Mapping[str, Mapping[str, Any]]) -> Iterator[None]:
    """Temporarily replace the overrides (tests, calibration previews)."""
    global _overrides
    saved = _overrides
    configure(params)
    try:
        yield
    finally:
        _overrides = saved
