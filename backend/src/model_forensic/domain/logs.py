"""Which of a run's log files says the most — the one to show first.

A run has many logs: the user script's ``std_log`` (one per process in distributed training), the v1 driver log,
library ``*.log`` files, and the platform's ``system_logs`` (lifecycle, capability, host tools) that rarely say anything
about training. A pipeline's own logs only say that its stages started. ``rank`` orders them: the training process's
log first (rank 0 of a distributed job), then the driver log, other logs of the script, and the platform's last;
within a group the larger file first (when the size is known), then empty files last.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from model_forensic.domain.artifacts import Artifact

_PROCESS = re.compile(r"process_?(\d+)", re.IGNORECASE)
_RANK = re.compile(r"(?:rank|node)_?(\d+)", re.IGNORECASE)

GROUPS = (
    # (group, label, matcher) — first match wins (the platform's own *.log files are not the script's)
    ("script", "training script output (std_log)", re.compile(r"(^|/)user_logs/std_?log[^/]*\.txt$", re.IGNORECASE)),
    ("driver", "driver log (v1)", re.compile(r"(^|/)azureml-logs/\d*_?driver_log[^/]*\.txt$", re.IGNORECASE)),
    (
        "script",
        "training script output",
        re.compile(r"(^|/)(std_?log[^/]*|stdout[^/]*|output)\.(txt|log)$", re.IGNORECASE),
    ),
    (
        "platform",
        "Azure ML platform log (job lifecycle)",
        re.compile(r"(^|/)(system_logs|azureml-logs)/", re.IGNORECASE),
    ),
    (
        "app",
        "application log",
        re.compile(r"(^|/)(logs|outputs|stage_output/logs)/.*\.(log|txt)$|\.log$", re.IGNORECASE),
    ),
    ("user", "other user log", re.compile(r"(^|/)user_logs/", re.IGNORECASE)),
)
# the order the groups are shown in
_ORDER = {g: i for i, g in enumerate(("script", "driver", "app", "user", "other", "platform"))}


def group_of(path: str) -> tuple[str, str]:
    """``(group, label)`` of a log path."""
    for g, label, rx in GROUPS:
        if rx.search(path):
            return g, label
    return "other", "log"


def _process(path: str) -> int:
    m = _PROCESS.search(path) or _RANK.search(path)
    return int(m.group(1)) if m else 0


def rank(a: Artifact) -> tuple[int, int, int, int, str]:
    """Sort key: smaller = more worth showing first."""
    g, _ = group_of(a.path)
    size = a.size_bytes
    empty = 1 if size == 0 else 0
    return (empty, _ORDER.get(g, len(_ORDER)), _process(a.path), -(size or 0), a.path)


def best_log(logs: Sequence[Artifact]) -> Artifact:
    """The log to show first (``logs`` must not be empty)."""
    return min(logs, key=rank)
