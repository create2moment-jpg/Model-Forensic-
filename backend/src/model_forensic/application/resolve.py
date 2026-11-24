"""Paste anything: a run id, ``<workspace>:<run id>``, an Azure ML studio link or a local folder → the run it names.

Read-only and index-first: the run index answers (no backend call for a bare id); a ``<workspace>:<id>`` the index
does not have is asked of that workspace once. A local folder that is not added yet is reported as such, so the UI can
offer to add it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

_STUDIO_SEG = re.compile(r"/(?:runs|jobs|run)/([^/?#]+)", re.IGNORECASE)


def _run(svc: Any, run: Any, how: str) -> dict[str, Any]:
    return {"kind": "run", "ref": str(run.ref), "name": run.display_name or run.name, "how": how}


def _by_id(svc: Any, run_id: str) -> list[Any]:
    res = svc.search(run_id, limit=25)
    return [r for r in res.runs if r.ref.run_id == run_id or r.name == run_id or r.display_name == run_id]


def resolve(svc: Any, text: str) -> dict[str, Any]:
    t = text.strip().strip('"').strip("'")
    if not t:
        return {"kind": "none"}
    # an Azure ML studio (or any) link: the run id is the path segment after /runs/ or /jobs/, or ?runId=
    if "://" in t:
        u = urlparse(t)
        q = parse_qs(u.query)
        m = _STUDIO_SEG.search(u.path)
        rid = unquote(m.group(1)) if m else (q.get("runId") or q.get("runid") or [""])[0]
        if rid:
            hits = _by_id(svc, rid)
            if hits:
                return _run(svc, hits[0], "Azure ML studio link")
            return {"kind": "none", "why": f"no indexed run {rid!r} — sync the workspace it belongs to"}
        return {"kind": "none", "why": "a link without a run id"}
    # a local folder
    if t.startswith(("/", "~", "./")) or re.match(r"^[A-Za-z]:\\", t):
        p = Path(t).expanduser()
        target = str(p.resolve()) if p.exists() else str(p)
        for r in svc.search("", limit=500).runs:
            path = str(r.backend_extras.get("path") or "")
            if path and (path == target or path.rstrip("/") == target.rstrip("/")):
                return _run(svc, r, "local folder")
        if p.is_dir():
            return {"kind": "folder", "path": target, "why": "not added yet — add it to analyse it in place"}
        return {"kind": "none", "why": "no such folder"}
    # <workspace>:<run id>
    if ":" in t and not t.startswith(":"):
        backend, _, rid = t.partition(":")
        if backend in svc.sources:
            try:
                return _run(svc, svc.get_run(t), "run reference")
            except Exception:
                return {"kind": "none", "why": f"{backend} has no run {rid!r}"}
    hits = _by_id(svc, t)
    if len(hits) == 1:
        return _run(svc, hits[0], "run id")
    if hits:
        return {"kind": "runs", "runs": [_run(svc, r, "run id") for r in hits[:10]]}
    return {"kind": "none"}
