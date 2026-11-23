"""Metric-name aliases for every workspace: ``config/metric-aliases.local.yaml`` (git-ignored).

    aliases:                      # a raw metric name (as a run logged it) → the name to use
      train_loss_step: train/loss
      val_mAP: val/mAP

Written by Compare → *Metric names* (suggested pairs between two runs) or by hand. Applied on top of a project
profile's naming (the profile's own aliases win), so a local Lightning run and an Azure ML run line up.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def path() -> Path:
    from model_forensic.shared.paths import home_path

    return home_path(os.environ.get("MF_METRIC_ALIASES", "config/metric-aliases.local.yaml"))


def load(p: Path | None = None) -> dict[str, str]:
    f = p or path()
    if not f.is_file():
        return {}
    try:
        doc = yaml.safe_load(f.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return {str(k): str(v) for k, v in (doc.get("aliases") or {}).items() if str(k).strip() and str(v).strip()}


def save(aliases: dict[str, str], p: Path | None = None) -> Path:
    clean = {str(k).strip(): str(v).strip() for k, v in aliases.items() if str(k).strip() and str(v).strip()}
    if len(clean) > 5000:
        raise ValueError("at most 5000 aliases")
    f = p or path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text("# Metric-name aliases for every workspace (git-ignored) — Compare → Metric names.\n"
                   + yaml.safe_dump({"aliases": dict(sorted(clean.items()))}, sort_keys=False))  # fmt: skip
    tmp.replace(f)
    return f
