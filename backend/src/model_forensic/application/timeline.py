"""A run as an explicit forensic timeline: stages and sub-stages, each with its duration and *where that number comes
from* — backend timestamps (measured) or metric timestamps (estimated) — plus the evidence available to it (configs,
metrics, files, logs, the code diff). Also the code diff itself, as raw evidence (never interpreted here).
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime, timezone
from typing import Any

from model_forensic.domain.metrics import Split
from model_forensic.projects.profile import ArtifactLayout, stage_role_default

MAX_DIFF_BYTES = 2 * 1024 * 1024


def _ts(dt: datetime | None) -> float | None:
    return dt.timestamp() if dt else None


def _iso(t: float | None) -> str | None:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat() if t is not None else None


def _span(name: str, start: float | None, end: float | None, source: str, **extra: Any) -> dict[str, Any] | None:
    if start is None or end is None or end < start:
        return None
    return {
        "name": name,
        "start": _iso(start),
        "end": _iso(end),
        "duration_s": round(end - start, 1),
        "source": source,
        **extra,
    }


def _metric_window(series: list[Any], splits: tuple[Split, ...]) -> tuple[float | None, float | None]:
    lo: float | None = None
    hi: float | None = None
    for s in series:
        if s.key.split not in splits or not s.timestamps:
            continue
        ts = [t for t in s.timestamps if t]
        if ts:
            lo = min(ts) if lo is None else min(lo, min(ts))
            hi = max(ts) if hi is None else max(hi, max(ts))
    return lo, hi


def sub_stages(run: Any, series: list[Any]) -> list[dict[str, Any]]:
    """Queue / setup (backend), training and evaluation (metric timestamps — estimated), finalising (estimated)."""
    created, started, ended = _ts(run.created_at), _ts(run.started_at), _ts(run.ended_at)
    out: list[dict[str, Any] | None] = []
    q = (
        _span("queued · setup", created, started, "backend timestamps")
        if created and started and started > created
        else None
    )
    if q:
        out.append(q)
    t0, t1 = _metric_window(series, (Split.TRAIN,))
    if t0 is not None and t1 is not None:
        out.append(_span("training", started or t0, t1, "metric timestamps (estimated)", estimated=True))
    e0, e1 = _metric_window(series, (Split.VAL, Split.TEST))
    if e0 is not None and e1 is not None:
        out.append(_span("evaluation", e0, e1, "metric timestamps (estimated)", estimated=True))
    last = max(x for x in (t1, e1) if x is not None) if (t1 or e1) else None
    # Estimates never leave the measured window of the run / stage: clamp, and say so.
    lo, hi = started or created, ended
    for sp in out:
        if sp is None or not sp.get("estimated"):
            continue
        a = datetime.fromisoformat(sp["start"]).timestamp()
        b = datetime.fromisoformat(sp["end"]).timestamp()
        ca = max(a, lo) if lo is not None else a
        cb = min(b, hi) if hi is not None else b
        if cb < ca:  # entirely outside: an estimate that cannot be placed honestly
            sp.update(start=None, end=None, duration_s=None)
            sp["note"] = "metric timestamps lie outside the measured window — not placed (clock skew or merged series)"
        elif (ca, cb) != (a, b):
            sp.update(start=_iso(ca), end=_iso(cb), duration_s=round(cb - ca, 1))
            sp["note"] = "metric timestamps fall outside the measured window — clamped (clock skew or merged series)"
    if last is not None and hi is not None:
        last = min(last, hi)
    if last is not None and ended and ended - last > 60:
        out.append(
            _span(
                "finalising · artifact upload", last, ended, "metric + backend timestamps (estimated)", estimated=True
            )
        )
    return [x for x in out if x]


def code_diff_globs(project: Any) -> list[str]:
    return list(project.artifacts.code_diff) if project is not None else ArtifactLayout().code_diff


def find_code_diffs(svc: Any, run: str) -> list[dict[str, Any]]:
    globs = code_diff_globs(svc.project_of(run) if hasattr(svc, "project_of") else None)
    out = []
    for a in svc.artifacts(run):
        name = a.path.lower()
        if any(fnmatch.fnmatchcase(name, g.lower()) for g in globs):
            out.append({"run": str(a.run), "stage": a.stage, "path": a.path, "size": a.size_bytes})
    return out


def diff_stats(text: str) -> dict[str, int]:
    files = len(re.findall(r"^diff --git ", text, re.M)) or len(re.findall(r"^\+\+\+ ", text, re.M))
    add = sum(1 for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    rem = sum(1 for line in text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return {"files": files, "additions": add, "deletions": rem}


def read_code_diff(svc: Any, run: str, stage: str | None = None) -> dict[str, Any]:
    """The code diff the job saved (raw text, at most 2 MB) with simple counts — evidence, not interpretation."""
    found = find_code_diffs(svc, run)
    if stage is not None:
        found = [f for f in found if (f["stage"] or "") == stage] or found
    if not found:
        return {
            "available": False,
            "searched": code_diff_globs(svc.project_of(run) if hasattr(svc, "project_of") else None),
        }
    f = found[0]
    a = svc.find_artifact(run, f["path"] if not f["stage"] else f"{f['run']}::{f['path']}") or svc.find_artifact(
        run, f["path"]
    )
    chunks: list[bytes] = []
    n = 0
    for c in svc.source(a.run).open_artifact(a.run.run_id, a.path):
        chunks.append(c)
        n += len(c)
        if n >= MAX_DIFF_BYTES:
            break
    text = b"".join(chunks)[:MAX_DIFF_BYTES].decode("utf-8", errors="replace")
    return {
        "available": True,
        "run": str(a.run),
        "stage": f["stage"],
        "path": a.path,
        "size": a.size_bytes,
        "truncated": n >= MAX_DIFF_BYTES,
        "stats": diff_stats(text),
        "text": text,
        "label": "raw evidence — the diff as the training job saved it",
        "others": [x for x in found[1:]],
    }


def _evidence(
    svc: Any, run_ref: Any, arts: list[Any], stage: str | None, diffs: list[dict[str, Any]]
) -> dict[str, Any]:
    mine = [a for a in arts if (a.stage or None) == stage] if stage is not None else [a for a in arts if not a.stage]
    try:
        cfgs = len([c for c in svc.configs(run_ref) if c.source != "params"])
    except Exception:  # evidence counts never fail the timeline
        cfgs = 0
    return {
        "files": len(mine),
        "configs": cfgs,
        "logs": sum(
            1 for a in mine if a.kind.value == "log" or a.path.startswith(("logs/", "user_logs/", "azureml-logs/"))
        ),
        "code_diff": next((d["path"] for d in diffs if (d["stage"] or None) == stage), None)
        or (next((d["path"] for d in diffs), None) if stage is None else None),
    }


def timeline(svc: Any, run: str) -> dict[str, Any]:
    r = svc.get_run(run)
    project = svc.project_of(run) if hasattr(svc, "project_of") else None
    role_of = project.stage_role if project is not None else stage_role_default
    arts = svc.artifacts(r.ref)
    diffs = find_code_diffs(svc, run)
    stages = svc.stages(r.ref)
    phases = []
    current = None
    for name, cref in stages:
        try:
            c = svc.get_run(cref)
        except Exception as exc:
            phases.append({"name": name, "role": role_of(name), "status": "unknown", "error": type(exc).__name__})
            continue
        try:
            series = svc.series(cref)
        except Exception:
            series = []
        span = _span(name, _ts(c.started_at), _ts(c.ended_at), "backend timestamps")
        if span is None and c.started_at and c.status.value == "running":
            span = _span(name, _ts(c.started_at), datetime.now(timezone.utc).timestamp(), "backend start · running")
        if current is None and c.status.value in ("running", "queued", "starting", "preparing"):
            current = name
        phases.append(
            {
                **(span or {"name": name, "start": None, "end": None, "duration_s": None, "source": "no timestamps"}),
                "role": role_of(name),
                "status": c.status.value,
                "run": str(cref),
                "command": c.command,
                "environment": c.environment,
                "compute": c.compute.target,
                "sub_stages": sub_stages(c, series),
                "evidence": {**_evidence(svc, cref, arts, name, diffs), "metrics": len(series)},
                "failure": c.failure.message if c.failure else None,
            }
        )
    own_series = svc.series(r.ref) if not stages else []
    if not stages:
        subs = sub_stages(r, own_series)
        if r.status.value == "running":  # what logged most recently is what is running now
            active = [x for x in subs if x["name"] in ("training", "evaluation")]
            current = (
                max(active, key=lambda x: x["end"])["name"] if active else (subs[-1]["name"] if subs else "starting")
            )
    else:
        subs = []
    return {
        "run": {
            "id": str(r.ref),
            "name": r.display_name or r.name,
            "experiment": r.experiment,
            "user": r.user,
            "status": r.status.value,
            "created": _iso(_ts(r.created_at)),
            "started": _iso(_ts(r.started_at)),
            "ended": _iso(_ts(r.ended_at)),
            "duration_s": r.duration_s,
            "current_stage": current,
            "command": r.command,
            "environment": r.environment,
            "compute": r.compute.target,
            "project": project.id if project is not None else None,
        },
        "stages": phases,
        "sub_stages": subs,
        "evidence": {**_evidence(svc, r.ref, arts, None, diffs), "metrics": len(own_series) if not stages else None},
        "code_diffs": diffs,
    }
