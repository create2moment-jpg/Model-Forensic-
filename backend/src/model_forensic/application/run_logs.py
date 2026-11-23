"""Scan every log file of a run and its pipeline stages for lines that matter.

The useful facts in driver logs (dataset versions loaded, GPUs detected, precision / recall lines) sit anywhere in
files that can be hundreds of MB, and for pipelines they are spread over the stage runs. ``scan_logs`` downloads
each log once into the artifact cache (later calls hit the cache), streams it line by line and keeps only the lines
that match the given patterns — never the whole file in memory. Every hit cites its stage and file; secrets (SAS
signatures, tokens) are redacted.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from model_forensic.domain.artifacts import Artifact, ArtifactKind
from model_forensic.shared.errors import NotFoundError
from model_forensic.shared.logging import get_logger, redact

log = get_logger(__name__)

_LOG_PATH = re.compile(r"(^|/)(user_logs|azureml-logs|logs|system_logs)/|std_?log|driver_log|\.log$", re.IGNORECASE)
_SAS = re.compile(r"(\?|&)(sv|sig|se|sp|st|spr|srt|ss|sr|skoid|sktid|skt|ske|sks|skv)=[^&\s'\"]+")


@dataclass
class LogHit:
    stage: str  # "" for the run itself
    path: str
    line_no: int
    line: str


@dataclass
class LogScan:
    hits: list[LogHit] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)  # {stage, path, size_bytes, scanned, error}
    notes: list[str] = field(default_factory=list)


def is_log(a: Artifact) -> bool:
    return a.kind == ArtifactKind.LOG or (a.path.endswith((".txt", ".log")) and bool(_LOG_PATH.search(a.path)))


def clean(line: str) -> str:
    return redact(_SAS.sub(lambda m: f"{m.group(1)}{m.group(2)}=<redacted>", line.rstrip("\n")))[:2000]


def scan_logs(
    svc: Any,
    ref: Any,
    patterns: Sequence[re.Pattern[str]],
    *,
    max_files: int = 40,
    max_file_bytes: int = 512 * 1024**2,
    max_hits: int = 2000,
) -> LogScan:
    """Lines matching any pattern across the run's and its stages' logs, largest file first (the driver log)."""
    out = LogScan()
    try:
        arts = [a for a in svc.artifacts(ref) if is_log(a)]
    except Exception as exc:
        out.notes.append(f"could not list the run's files: {type(exc).__name__}")
        return out
    if not arts:
        out.notes.append("the run has no log files")
        return out
    arts.sort(key=lambda a: -(a.size_bytes or 0))
    budget = int(getattr(svc, "auto_fetch_max_bytes", 50 * 1024**2))  # automatic downloads stay small
    spent = 0
    for a in arts[:max_files]:
        rec: dict[str, Any] = {"stage": a.stage or "", "path": a.path, "size_bytes": a.size_bytes, "scanned": False}
        out.files.append(rec)
        if a.size_bytes and a.size_bytes > max_file_bytes:
            rec["error"] = f"skipped: larger than {max_file_bytes // 1024**2} MB"
            continue
        if getattr(svc.sources.get(a.run.backend), "remote", True) and svc.artifact_store.cached(a) is None:
            if spent + (a.size_bytes or 0) > budget:
                rec["error"] = (
                    f"not downloaded: over the {budget // 1024**2} MB automatic limit — download it from Artifacts"
                )
                continue
            spent += a.size_bytes or 0
        try:
            local, _ = svc.artifact_store.fetch(svc.source(a.run), [a])
            path = local[0].local_path
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            continue
        rec["scanned"] = True
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if any(p.search(line) for p in patterns):
                        out.hits.append(LogHit(a.stage or "", a.path, i, clean(line)))
                        if len(out.hits) >= max_hits:
                            out.notes.append(f"stopped after {max_hits} matching lines")
                            return out
        except OSError as exc:
            rec["error"] = f"{type(exc).__name__}"
    if len(arts) > max_files:
        out.notes.append(f"scanned the {max_files} largest of {len(arts)} log files")
    return out


def file_id(a: Artifact, ref: Any) -> str:
    """How the UI names one file: ``path`` for the run's own, ``<stage run>::<path>`` for a stage's (``find_artifact``)."""
    return a.path if a.run == ref else f"{a.run}::{a.path}"


def log_catalog(svc: Any, ref: Any, stage: str | None = None) -> dict[str, Any]:
    """Every log of the run and of its pipeline stages (only that stage's with ``stage``), the most telling first.

    The order: a failed stage's logs first, then the stages that ran longest (training before evaluation), the
    pipeline's own (orchestration) logs last; within one run ``domain.logs.rank`` (the training process's std_log,
    rank 0 first, before platform logs). Listing only — no file is read."""
    from model_forensic.domain.logs import group_of, rank

    r = svc.ref(ref)
    arts = [a for a in svc.artifacts(r) if is_log(a) and (stage is None or a.stage == stage)]
    stages = svc.stages(r)
    info: dict[str, dict[str, Any]] = {}
    for i, (name, c) in enumerate(stages):
        try:
            run = svc.get_run(c)
            info[name] = {"failed": run.status.value == "failed", "duration": run.duration_s or 0.0, "order": i}
        except Exception:  # a stage that cannot be read still lists its files
            info[name] = {"failed": False, "duration": 0.0, "order": i}

    def key(a: Artifact) -> tuple[Any, ...]:
        if not a.stage:  # the run's own logs: a pipeline's say only that its stages started
            return (1 if stages else 0, 0, 0, 0, rank(a))
        st = info.get(a.stage, {"failed": False, "duration": 0.0, "order": 0})
        return (0, 0 if st["failed"] else 1, -st["duration"], st["order"], rank(a))

    arts.sort(key=key)
    files = []
    for a in arts:
        group, label = group_of(a.path)
        files.append(
            {
                "id": file_id(a, r),
                "path": a.path,
                "stage": a.stage,
                "run": str(a.run),
                "size_bytes": a.size_bytes,
                "group": group,
                "label": label,
                "failed_stage": bool(a.stage and info.get(a.stage, {}).get("failed")),
            }
        )
    return {
        "files": files,
        "recommended": files[0]["id"] if files else None,
        "stages": [n for n, _ in stages],
        "stage": stage,
    }


def read_log_file(svc: Any, ref: Any, fid: str, tail_bytes: int | None = 400_000) -> dict[str, Any]:
    """One log's text — its last ``tail_bytes`` (a ranged read where the backend supports it), ``None``: all of it."""
    r = svc.ref(ref)
    a = svc.find_artifact(r, fid)
    if a is None or not is_log(a):
        raise NotFoundError(f"no log {fid!r} in this run")
    src = svc.source(a.run)
    hit = svc.artifact_store.cached(a)
    size: int | None
    if hit is not None:  # downloaded before: read the copy
        local = hit.local_path
        size = local.stat().st_size
        with local.open("rb") as fh:
            if tail_bytes and size > tail_bytes:
                fh.seek(size - tail_bytes)
            data = fh.read()
    elif tail_bytes and hasattr(src, "read_tail"):
        data = src.read_tail(a.run.run_id, a.path, tail_bytes)
        size = a.size_bytes
    else:
        offset = max(0, (a.size_bytes or 0) - tail_bytes) if tail_bytes else 0
        data = b"".join(src.open_artifact(a.run.run_id, a.path, offset))
        if tail_bytes:
            data = data[-tail_bytes:]
        size = a.size_bytes
    text = data.decode("utf-8", errors="replace")
    total = size if size is not None else None
    partial = bool(tail_bytes and (total is None or total > len(data)) and len(data) >= tail_bytes)
    if partial:  # cut mid-line: start at the next full line
        text = text.split("\n", 1)[1] if "\n" in text else text
    return {
        "id": fid,
        "path": a.path,
        "stage": a.stage,
        "run": str(a.run),
        "size_bytes": total,
        "read_bytes": len(data),
        "partial": partial,
        "text": text,
    }
