"""Where the built web UI is: shared by the API (to serve it) and the configuration checks (to report it)."""

from __future__ import annotations

import os
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1]  # src/model_forensic


def web_dist_candidates() -> list[Path]:
    """Where the built UI is looked for, in order: MF_WEB_DIST, the checkout this code runs from (editable install),
    the Model Forensic home (MF_HOME / the checkout found from the working directory), the working directory, and a
    copy bundled into the package."""
    from model_forensic.shared.paths import mf_home

    env = os.environ.get("MF_WEB_DIST")
    out = [Path(env).expanduser()] if env else []
    out += [
        _PACKAGE.parents[2] / "web" / "dist",
        mf_home() / "web" / "dist",
        Path.cwd() / "web" / "dist",
        _PACKAGE / "api" / "static",
    ]
    seen: list[Path] = []
    for c in out:
        if c not in seen:
            seen.append(c)
    return seen


def web_dist() -> Path | None:
    """The first candidate that holds a built UI (index.html), or None."""
    return next((c for c in web_dist_candidates() if (c / "index.html").is_file()), None)
