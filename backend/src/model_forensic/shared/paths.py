"""Filesystem helpers: the two roots relative paths resolve against, and safe joins.

* **Project root** (``PROJECT_PATH``): your training project. Paths *you* give that point at project data —
  ``mf inspect outputs/_azure_artefacts``, a local runs folder, a knowledge source — resolve from here, whatever
  folder ``mf`` runs in. Unset: the current directory.
* **Model Forensic home** (``MF_HOME``): the Model Forensic folder (``config/``, ``.mf/``, ``harness/``,
  ``model-forensic.yaml``). Found automatically from where the package lives (a source checkout); ``MF_HOME``
  overrides it; unset and not found (a plain pip install): the current directory.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path, PurePosixPath

from model_forensic.shared.errors import ValidationError


def safe_join(root: Path, relative: str) -> Path:
    """Join an untrusted relative path under ``root``, rejecting traversal and absolute paths.

    Artifact names come from remote systems and are untrusted. ``..`` components, absolute
    paths, drive letters and NUL bytes are rejected; the resolved result must stay under root.
    """
    if not relative or "\x00" in relative:
        raise ValidationError("empty or invalid artifact path")
    rel = PurePosixPath(relative.replace("\\", "/"))
    if rel.is_absolute() or any(p == ".." for p in rel.parts) or ":" in rel.parts[0]:
        raise ValidationError(f"unsafe artifact path: {relative!r}")
    root_resolved = root.resolve()
    candidate = (root_resolved / Path(*rel.parts)).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValidationError(f"artifact path escapes cache root: {relative!r}")
    return candidate


PROJECT_ENV = "PROJECT_PATH"
HOME_ENV = "MF_HOME"


def project_root() -> Path:
    env = os.environ.get(PROJECT_ENV, "").strip()
    return Path(env).expanduser() if env else Path.cwd()


@lru_cache(maxsize=1)
def _found_home() -> Path | None:
    for p in Path(__file__).resolve().parents:
        if (p / "backend" / "pyproject.toml").is_file() and (p / "config").is_dir():
            return p
    return None


def mf_home() -> Path:
    env = os.environ.get(HOME_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return _found_home() or Path.cwd()


def project_path(p: str | Path) -> Path:
    """A path you give about project data: absolute (or ``~``) as is, relative → under the project root."""
    q = Path(os.path.expandvars(str(p))).expanduser()
    return q if q.is_absolute() else project_root() / q


def home_path(p: str | Path) -> Path:
    """A Model Forensic file (config, data dir, harness): absolute as is, relative → under the Model Forensic home."""
    q = Path(os.path.expandvars(str(p))).expanduser()
    return q if q.is_absolute() else mf_home() / q


def describe() -> dict[str, str]:
    """Where relative paths resolve (shown by ``mf paths`` and the doctor)."""
    return {
        "project_root": str(project_root()),
        "project_root_from": f"${PROJECT_ENV}" if os.environ.get(PROJECT_ENV, "").strip() else "current directory",
        "mf_home": str(mf_home()),
        "mf_home_from": f"${HOME_ENV}"
        if os.environ.get(HOME_ENV, "").strip()
        else ("Model Forensic checkout" if _found_home() else "current directory"),
        "cwd": str(Path.cwd()),
    }
