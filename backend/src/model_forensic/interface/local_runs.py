"""Local run folders to analyse in place: ``config/local-runs.local.yaml`` (git-ignored) — Settings → Local runs.

    folders:
      - /data/experiments/my-run          # one run …
      - ~/work/lightning_logs             # … or a folder of runs

Read-only: Model Forensic reads these folders as they are (``infrastructure/connectors/folder``); it never writes
into them, copies them or keeps them in sync. They appear as the workspace ``local-runs``.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

BACKEND = "local-runs"


def path() -> Path:
    from model_forensic.shared.paths import home_path

    return home_path(os.environ.get("MF_LOCAL_RUNS", "config/local-runs.local.yaml"))


def load(p: Path | None = None) -> list[str]:
    f = p or path()
    if not f.is_file():
        return []
    try:
        doc = yaml.safe_load(f.read_text()) or {}
    except yaml.YAMLError:
        return []
    return [str(x) for x in (doc.get("folders") or []) if str(x).strip()]


def save(folders: list[str], p: Path | None = None) -> Path:
    clean = list(dict.fromkeys(str(Path(x).expanduser()) for x in folders if str(x).strip()))
    if len(clean) > 200:
        raise ValueError("at most 200 local run folders")
    f = p or path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text("# Local run folders analysed in place (git-ignored) — Settings → Local runs.\n"
                   + yaml.safe_dump({"folders": clean}, sort_keys=False))  # fmt: skip
    tmp.replace(f)
    return f
