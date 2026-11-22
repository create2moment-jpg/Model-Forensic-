"""Storage locations and download parallelism: what is where, is it usable, what may be deleted, how many workers.

* :func:`validate_path` — exists or can be created, writable (a real test write), free space, the device type where
  the OS says (SSD / HDD on Linux), and whether it suits the role (fast vs bulk).
* :func:`describe` — every role with its effective path, size used, free space and what deleting it costs.
* :func:`suggest_workers` — a download-worker count from the CPUs this process may really use (cgroup quota aware)
  and the disk type, plus a *boost* value with its resource implications; never unbounded.
* :func:`save` — overrides go to ``config/storage.local.yaml`` (git-ignored); paths apply at the next start.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import yaml

from model_forensic.shared.paths import home_path

ROLES: dict[str, dict[str, str]] = {
    "artifacts": {
        "title": "Downloaded files",
        "speed": "bulk",
        "delete": "safe — files are downloaded again when needed; prefetch tasks then start over for those files",
    },
    "meta": {
        "title": "Run metadata cache",
        "speed": "fast",
        "delete": "safe — metadata is read from the backend again (run pages are slower once)",
    },
    "index": {
        "title": "Indexes and your work",
        "speed": "fast",
        "delete": "NOT safe as a whole — forensic.sqlite holds studies, findings, drafts, beliefs, prefetch tasks; "
        "index.sqlite (rebuilt by `mf sync`, loses favourites) and docs.sqlite (rebuilt) may be deleted",
    },
    "work": {
        "title": "Study books and reports",
        "speed": "fast",
        "delete": "safe — regenerate a book from its study",
    },
}
FILE_GUIDE: tuple[tuple[str, str, str], ...] = (
    ("forensic.sqlite", "required", "studies, findings, drafts, belief verdicts, jobs, prefetch tasks — back it up"),
    ("index.sqlite", "rebuildable", "run index — `mf sync` rebuilds it (favourite marks are lost)"),
    ("docs.sqlite", "rebuildable", "documentation search index — rebuilt on the next search"),
    ("artifacts/", "cache", "downloaded files (content-addressed, verified) — re-downloaded on demand"),
    ("artifacts/**/*.part", "resumable", "partial downloads — a stopped prefetch resumes from them; delete = restart"),
    ("meta/", "cache", "finished runs' metadata — re-read from the backend"),
    ("books/", "cache", "generated study books — regenerate from the study"),
    ("llm.json", "settings", "the LLM connection last saved in the UI (no key) — delete to fall back to the config"),
)


def _existing(p: Path) -> Path:
    return p if p.exists() else next((q for q in p.parents if q.exists()), Path("/"))


def device_kind(p: Path) -> str:
    """'ssd', 'hdd' or 'unknown' (Linux: /sys/block/<dev>/queue/rotational of the filesystem's device)."""
    try:
        dev = _existing(p).stat().st_dev
        major, minor = os.major(dev), os.minor(dev)
        link = Path(f"/sys/dev/block/{major}:{minor}")
        if not link.exists():
            return "unknown"
        base = link.resolve()
        for cand in (base, base.parent):
            rot = cand / "queue" / "rotational"
            if rot.is_file():
                return "hdd" if rot.read_text().strip() == "1" else "ssd"
    except (OSError, ValueError):
        pass
    return "unknown"


def _size(p: Path, limit: int = 200_000) -> int:
    if p.is_file():
        return p.stat().st_size
    total, n = 0, 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                continue
            n += 1
            if n >= limit:
                return total
    return total


def validate_path(path: str | Path, role: str = "artifacts", min_free_gb: float = 1.0) -> dict[str, Any]:
    """Can this folder serve this role? Never creates anything except a temporary probe file (removed)."""
    p = home_path(path)
    problems: list[str] = []
    notes: list[str] = []
    existed = p.exists()
    if existed and not p.is_dir():
        problems.append("exists but is not a folder")
    parent = _existing(p)
    creatable = existed or (parent.is_dir() and os.access(parent, os.W_OK))
    if not creatable:
        problems.append(
            f"cannot be created: {parent} is a file"
            if not parent.is_dir()
            else f"cannot be created: {parent} is not writable"
        )
    writable = False
    if existed and p.is_dir():
        probe = p / f".mf-write-probe-{uuid.uuid4().hex[:8]}"
        try:
            probe.write_bytes(b"ok")
            writable = True
        except OSError as exc:
            problems.append(f"not writable ({exc.strerror or type(exc).__name__})")
        finally:
            probe.unlink(missing_ok=True)
    elif creatable:
        writable = True
        notes.append("will be created at start")
    try:
        free = shutil.disk_usage(parent).free
    except OSError:
        free = None
    if free is not None and free < min_free_gb * 1024**3:
        problems.append(f"only {free / 1e9:.1f} GB free (need ≥ {min_free_gb:g} GB)")
    kind = device_kind(p)
    want = ROLES.get(role, {}).get("speed", "bulk")
    if want == "fast" and kind == "hdd":
        notes.append("on a rotating disk — indexes and caches will be slower; an SSD is recommended for this role")
    if want == "bulk" and kind == "ssd" and free is not None and free < 50 * 1024**3:
        notes.append("SSD with little room — fine, but large checkpoints fill it quickly")
    return {
        "path": str(p),
        "role": role,
        "exists": existed,
        "creatable": creatable,
        "writable": writable,
        "free_bytes": free,
        "device": kind,
        "ok": not problems,
        "problems": problems,
        "notes": notes,
    }


def describe(settings: Any, store: Any = None) -> dict[str, Any]:
    paths = settings.paths()
    roles = []
    for role, p in paths.items():
        v = validate_path(p, role)
        used = (
            _size(p)
            if p.exists() and role != "index"
            else sum(
                (p / f).stat().st_size for f in ("forensic.sqlite", "index.sqlite", "docs.sqlite") if (p / f).is_file()
            )
        )
        roles.append(
            {**ROLES[role], **v, "used_bytes": used, "configured": getattr(settings.storage, f"{role}_dir") is not None}
        )
    return {
        "data_dir": str(settings.data_dir),
        "roles": roles,
        "files": [{"path": a, "kind": b, "what": c} for a, b, c in FILE_GUIDE],
        "workers": suggest_workers(settings.download_workers, paths["artifacts"]),
        "restart_needed_for": ["artifacts", "meta", "index", "work"],
    }


def usable_cpus() -> int:
    """CPUs this process may use: affinity, then a cgroup v2 quota (containers), then the machine count."""
    n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    try:
        q, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if q != "max":
            n = min(n, max(1, int(int(q) / int(period))))
    except (OSError, ValueError):
        pass
    return max(1, n)


def suggest_workers(current: int, artifacts_dir: Path) -> dict[str, Any]:
    """Downloads are network-bound: ~2 workers per CPU, capped; fewer on a rotating disk; boost doubles it."""
    cpus = usable_cpus()
    kind = device_kind(artifacts_dir)
    rec = min(16, max(2, cpus * 2))
    if kind == "hdd":
        rec = min(rec, 4)
    boost = min(32, rec * 2)
    return {
        "cpus": cpus,
        "device": kind,
        "current": current,
        "recommended": rec,
        "boost": boost,
        "boost_implications": (
            f"{boost} parallel connections: faster on a fast network, but more memory (~{boost * 8} MB of buffers), "
            "more load on the storage account (it may throttle with HTTP 429 — retries slow it down again) and "
            + ("a rotating disk that seeks more" if kind == "hdd" else "more disk writes in parallel")
        ),
        "why": f"{cpus} usable CPU(s), artifacts on {kind} storage; downloads wait on the network, so about two per CPU",
    }


def save(updates: dict[str, Any]) -> Path:
    """Write overrides to config/storage.local.yaml (git-ignored). Paths apply at the next start."""
    p = home_path("config/storage.local.yaml")
    cur: dict[str, Any] = {}
    if p.is_file():
        cur = yaml.safe_load(p.read_text()) or {}
    st = dict(cur.get("storage") or {})
    for role in ROLES:
        if f"{role}_dir" in updates:
            v = updates[f"{role}_dir"]
            if v:
                st[f"{role}_dir"] = str(v)
            else:
                st.pop(f"{role}_dir", None)
    cur["storage"] = st
    if "download_workers" in updates:
        n = int(updates["download_workers"])
        if not 1 <= n <= 64:
            raise ValueError("download_workers must be between 1 and 64")
        cur["download_workers"] = n
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# Model Forensic storage overrides (git-ignored) — written by Settings → Storage.\n"
        + yaml.safe_dump(cur, sort_keys=False),
        encoding="utf-8",
    )
    return p
