"""Pick a local run folder without typing its path: list the folders of a directory, complete a typed path.

A browser cannot hand a page the real path of a folder chosen in the system's file dialog, but the Model Forensic
server runs on the same machine (local mode) and can list its folders. Only folder names are returned — never file
contents — and each folder is marked when it already looks like a run (a metrics file, TensorBoard events, Lightning
logs, evaluation JSONs directly inside), so the right one is easy to spot. Hidden folders are left out unless the typed
prefix starts with a dot. Listings are capped; nothing is written.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MAX_DIRS = 500
MAX_PEEK = 200  # entries looked at inside a folder to say whether it looks like a run
_RUN_FILES = ("metrics.csv", "metrics.jsonl", "hparams.yaml")
_RUN_DIRS = ("lightning_logs", "tensorboard", "tb_logs", "version_0", "checkpoints", "eval", "evaluation")


def _peek(d: Path) -> str:
    """What makes this folder look like a run (``""``: nothing seen in its first entries)."""
    try:
        with os.scandir(d) as it:
            for i, e in enumerate(it):
                if i >= MAX_PEEK:
                    break
                n = e.name
                if n.startswith("events.out.tfevents"):
                    return "TensorBoard"
                if n in _RUN_FILES:
                    return n
                if n in _RUN_DIRS and e.is_dir(follow_symlinks=False):
                    return f"{n}/"
                if n.endswith(".json") and "eval" in n.lower():
                    return "evaluation files"
    except OSError:
        return ""
    return ""


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(path.strip() or "~")).expanduser()


def browse(path: str = "", hidden: bool = False) -> dict[str, Any]:
    """The sub-folders of ``path`` (default: the home folder), with the parent and a few starting points."""
    p = _expand(path)
    home = Path.home()
    roots = [str(home)] + [str(r) for r in (Path("/mnt"), Path("/data"), Path("/Volumes")) if r.is_dir()]
    if not p.is_dir():
        return {"ok": False, "path": str(p), "problem": "not a folder (or not readable)", "parent": None,
                "dirs": [], "home": str(home), "roots": roots}  # fmt: skip
    p = p.resolve()
    dirs: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(p) as it:
            entries = sorted((e for e in it if e.is_dir()), key=lambda e: e.name.lower())
    except OSError as exc:
        return {"ok": False, "path": str(p), "problem": f"cannot list: {type(exc).__name__}",
                "parent": str(p.parent) if p.parent != p else None, "dirs": [], "home": str(home), "roots": roots}  # fmt: skip
    for e in entries:
        if e.name.startswith(".") and not hidden:
            continue
        if len(dirs) >= MAX_DIRS:
            truncated = True
            break
        d = Path(e.path)
        dirs.append({"name": e.name, "path": str(d), "run": _peek(d)})
    return {
        "ok": True,
        "path": str(p),
        "parent": str(p.parent) if p.parent != p else None,
        "dirs": dirs,
        "truncated": truncated,
        "run": _peek(p),
        "home": str(home),
        "roots": roots,
    }


def complete(prefix: str, limit: int = 20) -> list[str]:
    """Folders whose path starts with ``prefix`` (``~`` and ``$VARS`` expanded): ``/data/ru`` → ``/data/runs/``.
    A prefix ending in ``/`` lists that folder's sub-folders."""
    if not prefix.strip():
        return []
    raw = prefix.strip()
    p = _expand(raw)
    if raw.endswith(("/", os.sep)):
        base, stem = p, ""
    else:
        base, stem = p.parent, p.name
    if not base.is_dir():
        return []
    out: list[str] = []
    try:
        with os.scandir(base) as it:
            names = sorted((e.name for e in it if e.is_dir()), key=str.lower)
    except OSError:
        return []
    low = stem.lower()
    for n in names:
        if n.startswith(".") and not stem.startswith("."):
            continue
        if n.lower().startswith(low):
            out.append(str(base / n) + os.sep)
            if len(out) >= limit:
                break
    return out
