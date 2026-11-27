"""The Resources view: how a run used its machines, and what that says — deterministic checks on the curves.

Sources, in order: the backend's machine metrics (Azure Monitor; a local run's ``system_metrics.jsonl``), else the
system metrics the run logged itself (MLflow ``system/…``). A finished run's answer is kept (it never changes), so the
view costs Azure Monitor a handful of reads once per run.

Checks (the steady part only — the first and last 5 % of the run are left out, start-up and shut-down are not the
pattern): GPUs mostly idle (and whether the CPU is the reason), GPU memory headroom or near out-of-memory, one node
lagging the others, disk nearly full.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from model_forensic.domain.resources import LABEL, ResourceSeries, ResourceUsage, logged_kind
from model_forensic.shared.errors import PermissionDeniedError

GPU_IDLE = 60.0  # mean GPU utilisation below this: the GPUs wait
CPU_BUSY = 85.0  # mean CPU utilisation above this while GPUs wait: input pipeline bound
MEM_HEADROOM = 45.0  # peak GPU memory below this: room for a larger batch
MEM_FULL = 97.0  # peak GPU memory above this: near out-of-memory
STRAGGLER = 0.75  # a node whose mean GPU utilisation is below this share of the best node's
DISK_FULL = 90.0

_cache: dict[str, dict[str, Any]] = {}
_lock = Lock()


def _steady(s: ResourceSeries) -> list[float]:
    n = len(s.v)
    cut = n // 20
    return s.v[cut : n - cut] if n > 20 else s.v


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def summarise(usage: ResourceUsage) -> dict[str, Any]:
    """Per kind: mean and peak over the steady part, per node and overall."""
    out: dict[str, Any] = {}
    for kind in LABEL:
        ss = [s for s in usage.series if s.kind == kind]
        if not ss:
            continue
        nodes: dict[str, list[float]] = {}
        for s in ss:
            nodes.setdefault(s.node, []).extend(_steady(s))
        allv = [x for v in nodes.values() for x in v]
        out[kind] = {
            "label": LABEL[kind],
            "mean": _mean(allv),
            "peak": max((max(s.v) for s in ss if s.v), default=None),
            "nodes": {n: _mean(v) for n, v in sorted(nodes.items())},
        }
    return out


def findings(summary: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    g, c, m, d = (summary.get(k) for k in ("gpu_util", "cpu_util", "gpu_mem", "disk_used"))
    if g and g["mean"] is not None and g["mean"] < GPU_IDLE:
        cpu = c["mean"] if c else None
        if cpu is not None and cpu >= CPU_BUSY:
            out.append({"level": "problem", "title": f"GPUs busy only {g['mean']:.0f}% of the time — the CPU is at {cpu:.0f}%",
                        "detail": "The input pipeline cannot feed the GPUs: more data-loader workers, lighter augmentation "
                                  "on the CPU, caching or faster storage. Every hour here pays for idle GPUs."})  # fmt: skip
        else:
            out.append({"level": "attention", "title": f"GPUs busy only {g['mean']:.0f}% of the time",
                        "detail": "The CPU is not the limit — look at I/O (reading from a mounted datastore), "
                                  "synchronisation between processes, or a small batch."})  # fmt: skip
    if m and m["peak"] is not None:
        if m["peak"] >= MEM_FULL:
            out.append({"level": "attention", "title": f"GPU memory peaked at {m['peak']:.0f}%",
                        "detail": "Close to out-of-memory: a longer sequence or a larger batch will fail."})  # fmt: skip
        elif m["peak"] < MEM_HEADROOM:
            out.append({"level": "info", "title": f"GPU memory peaked at {m['peak']:.0f}%",
                        "detail": "Room for a larger batch (or a larger model) on the same GPUs."})  # fmt: skip
    if g and len(g["nodes"]) > 1:
        means = {n: v for n, v in g["nodes"].items() if v is not None}
        if means:
            best = max(means.values())
            slow = [n for n, v in means.items() if best > 0 and v < STRAGGLER * best]
            for n in slow:
                out.append({"level": "attention", "title": f"{n} lags: GPUs {means[n]:.0f}% busy vs {best:.0f}% on the best node",
                            "detail": "In data-parallel training every step waits for the slowest node — a larger data "
                                      "shard, slower disk or a faulty GPU there slows the whole run."})  # fmt: skip
    if d and d["peak"] is not None and d["peak"] >= DISK_FULL:
        out.append({"level": "problem", "title": f"Disk {d['peak']:.0f}% full",
                    "detail": "Checkpoints or caches can fail to write."})  # fmt: skip
    return out


def _logged(svc: Any, run: Any) -> ResourceUsage | None:
    """The system metrics the run logged itself (MLflow ``system/gpu_0_utilization_percentage`` …)."""
    src = svc.source(run.ref)
    try:
        keys = [k for k in src.list_metric_keys(run.ref.run_id) if logged_kind(str(k))]
    except Exception:
        return None
    if not keys:
        return None
    series = []
    for s in src.get_metric_series(run.ref.run_id, keys):
        kk = logged_kind(str(s.key))
        if kk is None:
            continue
        xs = s.timestamps if s.timestamps is not None else s.steps
        pts = [(float(t), float(v)) for t, v in zip(xs, s.values, strict=False) if v is not None and v == v]
        series.append(ResourceSeries(kind=kk[0], device=kk[1], t=[p[0] for p in pts], v=[p[1] for p in pts]))
    return ResourceUsage(source="logged by the run", series=series) if series else None


def run_resources(svc: Any, ref: Any) -> dict[str, Any]:
    run = svc.get_run(ref)
    key = str(run.ref)
    if run.status.terminal and key in _cache:
        return _cache[key]
    base: dict[str, Any] = {
        "run": key,
        "status": run.status.value,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
    }
    if run.started_at is None:
        return {**base, "state": "not_started", "series": [], "summary": {}, "findings": [], "notes": [],
                "source": None, "access": None}  # fmt: skip
    end = run.ended_at or datetime.now(timezone.utc)
    access = None
    usage: ResourceUsage | None = None
    notes: list[str] = []
    fn = getattr(svc.source(run.ref), "resource_usage", None)
    if fn is not None:
        try:
            usage = fn(run.ref.run_id, run.started_at, end)
        except PermissionDeniedError as exc:
            access = exc.message
        except Exception as exc:  # the view still shows what the run logged
            notes.append(f"machine metrics unavailable: {type(exc).__name__}: {str(exc)[:200]}")
    if usage is None or not usage.series:
        usage = _logged(svc, run) or usage
    series = usage.series if usage else []
    summ = summarise(usage) if usage else {}
    out = {
        **base,
        "state": "ok" if series else "none",
        "source": usage.source if usage and series else None,
        "access": access,
        "notes": notes + (usage.notes if usage else []),
        "nodes": sorted({s.node for s in series}),
        "series": [s.model_dump() for s in series],
        "summary": summ,
        "findings": findings(summ),
    }
    if run.status.terminal and access is None and not notes:
        with _lock:
            _cache[key] = out
    return out
