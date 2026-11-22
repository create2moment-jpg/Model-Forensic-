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
    for a in arts[:max_files]:
        rec: dict[str, Any] = {"stage": a.stage or "", "path": a.path, "size_bytes": a.size_bytes, "scanned": False}
        out.files.append(rec)
        if a.size_bytes and a.size_bytes > max_file_bytes:
            rec["error"] = f"skipped: larger than {max_file_bytes // 1024**2} MB"
            continue
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
