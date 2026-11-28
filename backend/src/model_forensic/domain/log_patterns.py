"""What to pull out of a run's log files: patterns, each a regular expression with named groups.

A pattern is one of three kinds:

* ``event`` — each matching line is something that happened (a checkpoint saved, an error, a warning);
* ``fact`` — the last match of each named group is a fact about the run (world size, versions, dataset loaded);
* ``series`` — numeric named groups over ``step_field`` (or the line order) become curves (loss, data time, speed).

Patterns come from four places: built in (the v1 tool's log readers and common failure signatures), the training
codebase (harness task ``log_patterns``: a coding agent reads the code's print / log statements), the local LLM
(proposed from the run's own unexplained lines — kept only when the regex compiles and matches the line it cites),
and the user. Anything not built in is *proposed* until accepted.
"""

from __future__ import annotations

import re

from model_forensic.domain.base import DomainModel

CATEGORIES = ("environment", "data", "progress", "checkpoint", "evaluation", "warning", "error")
KINDS = ("event", "fact", "series")
SEVERITIES = ("info", "attention", "problem")
SOURCES = ("builtin", "code", "llm", "user")
STATUSES = ("active", "proposed", "rejected")
MAX_REGEX = 600


class LogPattern(DomainModel):
    id: str
    title: str
    regex: str
    category: str = "progress"
    kind: str = "event"
    severity: str = "info"
    step_field: str | None = None  # series: the group that is the x axis (else the line number)
    flags: str = ""  # "i": ignore case
    source: str = "user"
    status: str = "active"
    example: str | None = None  # a line it matches, verbatim
    code_ref: str | None = None  # file:line in the training code that prints it (patterns from the codebase)
    explain: str | None = None  # what it means / what to do

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.regex, re.IGNORECASE if "i" in self.flags else 0)


def check(p: LogPattern) -> list[str]:
    """Why a pattern cannot be used (empty: it can) — a bad pattern never reaches the extractor."""
    errs = []
    if p.category not in CATEGORIES:
        errs.append(f"category must be one of {', '.join(CATEGORIES)}")
    if p.kind not in KINDS:
        errs.append(f"kind must be one of {', '.join(KINDS)}")
    if p.severity not in SEVERITIES:
        errs.append(f"severity must be one of {', '.join(SEVERITIES)}")
    if len(p.regex) > MAX_REGEX:
        errs.append(f"regex longer than {MAX_REGEX} characters")
    try:
        rx = p.compiled()
    except re.error as exc:
        return [*errs, f"regex does not compile: {exc}"]
    if p.kind in ("fact", "series") and not rx.groupindex:
        errs.append(f"a {p.kind} pattern needs named groups (?P<name>…)")
    if p.step_field and p.step_field not in rx.groupindex:
        errs.append(f"step_field {p.step_field!r} is not a named group")
    if p.example is not None and not rx.search(p.example):
        errs.append("the regex does not match its own example line")
    return errs


_NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-f]{12,}\b")
_PATHLIKE = re.compile(r"(/[\w.@-]+){2,}")


def template(line: str) -> str:
    """A line with its variable parts replaced (numbers → <n>, ids → <id>, paths → <path>): lines that differ only
    in values share one template, so 10,000 progress lines become one."""
    t = _HEX.sub("<id>", line.strip())
    t = _PATHLIKE.sub("<path>", t)
    t = _NUM.sub("<n>", t)
    return re.sub(r"\s+", " ", t)[:300]


_NUM_RX = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?|nan|inf"


def from_line(line: str, pid: str, title: str | None = None) -> LogPattern | None:
    """A series pattern written from one line's numbers next to a name (``loss=3.5``, ``lr: 2e-4``, ``step 50``) —
    the deterministic proposal when no LLM is connected."""
    parts: list[str] = []
    names: list[str] = []
    for m in re.finditer(rf"\b(?P<key>[A-Za-z][\w/]*)(?P<sep>\s*[=:]\s*|\s+)(?P<val>{_NUM_RX})(?![\w.])", line, re.I):
        key = m.group("key")
        if m.group("sep").strip() == "" and key.lower() not in ("step", "iter", "iteration", "epoch"):
            continue  # "word 12" counts only for step / epoch counters
        name = re.sub(r"\W", "_", key.lower()).strip("_")[:30]
        if not name or name in names:
            continue
        names.append(name)
        sep = r"\s*[=:]\s*" if m.group("sep").strip() else r"\s+"
        parts.append(rf"\b{re.escape(key)}{sep}(?P<{name}>{_NUM_RX})")
    if not names:
        return None
    step = next((n for n in names if n in ("step", "iter", "iteration", "global_step")), None)
    p = LogPattern(id=pid, title=title or f"{', '.join(names)} (from the log)", regex=".*?".join(parts), kind="series",
                   category="progress", step_field=step, flags="i", source="llm", status="proposed", example=line.strip())  # fmt: skip
    return p if not check(p) else None


def _b(
    pid: str, title: str, regex: str, category: str, kind: str = "event", severity: str = "info", **kw: str
) -> LogPattern:
    return LogPattern(
        id=pid,
        title=title,
        regex=regex,
        category=category,
        kind=kind,
        severity=severity,
        source="builtin",
        status="active",
        **kw,
    )


BUILTIN: tuple[LogPattern, ...] = (
    # environment — from the v1 tool's readers (compute_details, dataset_provenance) and common banners
    _b("gpu.detected", "GPUs detected", r"NvidiaGpuInfo\((?P<gpu>[^)]*)\)", "environment", "fact"),
    _b(
        "gpu.none",
        "No GPU detected",
        r"Detected GPUs: \[\s*\]",
        "environment",
        severity="problem",
        explain="The job ran without a GPU — wrong compute target or a driver problem.",
    ),
    _b(
        "env.world_size",
        "World size",
        r"\bworld[_ ]size\s*[=:]\s*(?P<world_size>\d+)",
        "environment",
        "fact",
        flags="i",
    ),
    _b(
        "env.torch",
        "PyTorch version",
        r"\btorch(?:\s+version)?\s*[=:]\s*(?P<torch>\d+\.\d+[\w.+-]*)",
        "environment",
        "fact",
        flags="i",
    ),
    _b(
        "env.cuda",
        "CUDA version",
        r"\bcuda(?:\s+version)?\s*[=:]\s*(?P<cuda>\d+\.\d+)",
        "environment",
        "fact",
        flags="i",
    ),
    _b("data.loading", "Dataset loaded", r"loading dataset (?P<dataset>[^\s:]+):(?P<version>[^,\s]+)", "data", "fact"),
    _b(
        "data.batch_override",
        "Batch size / workers overridden",
        r"\b(?P<key>batch_size_\w+|num_workers_\w+)\s*[=:]\s*(?P<value>\d+)",
        "data",
        "fact",
    ),
    # progress
    _b(
        "progress.step_loss",
        "Loss by step",
        rf"\b(?:step|iter(?:ation)?)\s*[=: ]\s*(?P<step>\d+)\b.*?\bloss\s*[=:]\s*(?P<loss>{_NUM_RX})",
        "progress",
        "series",
        step_field="step",
        flags="i",
    ),
    _b(
        "progress.epoch",
        "Epoch",
        r"\bepoch\s*[=:\[ ]\s*(?P<epoch>\d+)(?:\s*/\s*(?P<epochs>\d+))?",
        "progress",
        "fact",
        flags="i",
    ),
    _b(
        "progress.data_time",
        "Data loading time",
        rf"\bdata[_ ]time\s*[=:]\s*(?P<data_time>{_NUM_RX})",
        "progress",
        "series",
        flags="i",
        explain="Time waiting for the next batch: when it is a large share of the step time, the input pipeline is the bottleneck.",
    ),
    _b(
        "progress.throughput",
        "Throughput",
        r"(?P<throughput>\d+(?:\.\d+)?)\s*(?:samples|imgs?|images|it)/s\b",
        "progress",
        "series",
        flags="i",
    ),
    _b("checkpoint.saved", "Checkpoint saved", r"\b(?:sav(?:ed|ing))\b.*\bcheckpoint\b", "checkpoint", flags="i"),
    _b(
        "eval.result",
        "Evaluation result",
        rf"\b(?:val|eval|validation|test)\b.*?\b(?P<metric>mAP|NDS|AMOTA|AP|accuracy|acc)\b\s*[=:]\s*(?P<value>{_NUM_RX})",
        "evaluation",
        "fact",
        flags="i",
    ),
    # problems
    _b("error.traceback", "Python traceback", r"Traceback \(most recent call last\)", "error", severity="problem"),
    _b(
        "error.exception",
        "Exception raised",
        r"^\s*(?P<exception>[A-Z]\w*(?:Error|Exception))\s*:\s*(?P<message>.+)$",
        "error",
        severity="problem",
    ),
    _b(
        "error.cuda_oom",
        "CUDA out of memory",
        r"CUDA out of memory|OutOfMemoryError",
        "error",
        severity="problem",
        explain="A smaller batch, gradient accumulation, mixed precision or activation checkpointing.",
    ),
    _b(
        "error.nccl",
        "NCCL / distributed error",
        r"NCCL (?:WARN|ERROR)|ncclInternalError|ncclSystemError|Watchdog caught collective operation timeout",
        "error",
        severity="problem",
        explain="A node or GPU dropped out of a collective: network, a hung rank, or one rank crashing first.",
    ),
    _b(
        "error.nan",
        "NaN / Inf",
        r"\b(?:loss|grad\w*)\b[^\n]{0,20}\b(?:is |became |=\s*)?(?:nan|inf)\b|non-finite",
        "error",
        severity="problem",
        flags="i",
        explain="Divergence: learning rate, warm-up, a bad batch, or mixed-precision overflow.",
    ),
    _b(
        "error.killed",
        "Process killed",
        r"\bKilled\b|SIGKILL|exit code 137|oom-kill",
        "error",
        severity="problem",
        explain="Exit 137 / SIGKILL is usually host memory (RAM) running out — dataloader workers or caching.",
    ),
    _b(
        "error.dataloader",
        "Dataloader worker died",
        r"DataLoader worker \(pid \d+\) (?:is killed|exited unexpectedly)",
        "error",
        severity="problem",
    ),
    _b("error.level", "ERROR / CRITICAL line", r"\b(?:ERROR|CRITICAL|FATAL)\b", "error", severity="attention"),
    _b(
        "warning.level",
        "Warning",
        r"\bWARN(?:ING)?\b|UserWarning|DeprecationWarning|RuntimeWarning",
        "warning",
        severity="attention",
    ),
)

# lines worth explaining even when no pattern covers them (candidates for a new pattern)
NOTEWORTHY = re.compile(
    r"error|exception|traceback|warn|fail|nan\b|inf\b|killed|timeout|timed out|oom|denied|retry", re.I
)
