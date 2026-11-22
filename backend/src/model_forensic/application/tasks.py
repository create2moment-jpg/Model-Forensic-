"""Application-scoped background tasks with declared cost — the one authoritative source of "what is running".

* :class:`TaskSpec` (from the master configuration ``config/tasks.yaml`` + git-ignored ``config/tasks.local.yaml``):
  a task kind's cost class (lightweight / medium / heavy / very_heavy), why, whether it is cacheable, its
  dependencies, preferred concurrency and whether Light Mode runs it.
* :class:`TaskManager` runs tasks on a bounded pool, never twice at once (the same kind + subject joins the running
  one), resolves dependencies first, records state, progress, timings, cache state (cold / warm) and errors, and keeps
  a measured history per kind (p50 / p90 / p99) that the Test Space turns into classification proposals.

Tasks belong to the application, not to a page: switching tabs or reloading the UI never stops, resets or hides them.
"""

from __future__ import annotations

import builtins
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from model_forensic.shared.logging import get_logger

log = get_logger(__name__)

CostClass = Literal["lightweight", "medium", "heavy", "very_heavy"]
State = Literal["queued", "waiting", "running", "done", "failed", "cancelled"]
COST_ORDER: tuple[str, ...] = ("lightweight", "medium", "heavy", "very_heavy")


@dataclass(frozen=True)
class TaskSpec:
    kind: str
    title: str
    cost: str = "medium"
    why: str = ""
    cacheable: bool = True
    light_mode: bool = False
    depends_on: tuple[str, ...] = ()
    concurrency: int = 4
    prerequisites: tuple[str, ...] = ()


def load_specs(core: Path, local: Path | None = None) -> dict[str, TaskSpec]:
    """Core defaults (committed, project-agnostic) overlaid by local reviewed changes."""
    docs: list[dict[str, Any]] = []
    for p in (core, local):
        if p is not None and p.is_file():
            docs.append(yaml.safe_load(p.read_text()) or {})
    merged: dict[str, dict[str, Any]] = {}
    for d in docs:
        for kind, v in (d.get("tasks") or {}).items():
            merged.setdefault(kind, {}).update(v or {})
    out = {}
    for kind, v in merged.items():
        if v.get("cost", "medium") not in COST_ORDER:
            raise ValueError(f"task {kind}: cost must be one of {', '.join(COST_ORDER)}")
        out[kind] = TaskSpec(
            kind=kind,
            title=str(v.get("title", kind)),
            cost=str(v.get("cost", "medium")),
            why=str(v.get("why", "")),
            cacheable=bool(v.get("cacheable", True)),
            light_mode=bool(v.get("light_mode", False)),
            depends_on=tuple(v.get("depends_on") or ()),
            concurrency=int(v.get("concurrency", 4)),
            prerequisites=tuple(v.get("prerequisites") or ()),
        )
    return out


@dataclass
class TaskRecord:
    id: str
    kind: str
    subject: str
    title: str
    cost: str
    why: str
    state: State = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    last_activity: float = field(default_factory=time.time)
    progress: dict[str, Any] = field(default_factory=dict)
    cache: str = "unknown"  # cold | warm | unknown — was the answer already cached when it ran?
    error: str = ""
    waiting_on: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_s(self) -> float:
        if self.started_at is None:
            return 0.0
        return (self.ended_at or time.time()) - self.started_at

    def view(self) -> dict[str, Any]:
        d = asdict(self)
        d["elapsed_s"] = round(self.elapsed_s, 3)
        d["active"] = self.state in ("queued", "waiting", "running")
        return d


TaskFn = Callable[[TaskRecord], dict[str, Any] | None]


class TaskManager:
    """Runs and remembers tasks for the whole application (bounded pool, deduplicated, dependency-aware)."""

    def __init__(self, specs: dict[str, TaskSpec], docs: Any = None, workers: int = 8) -> None:
        self.specs = specs
        self.docs = docs
        self._pool = ThreadPoolExecutor(max_workers=max(2, workers), thread_name_prefix="mf-task")
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._sems: dict[str, threading.BoundedSemaphore] = {}
        self._done = threading.Condition(self._lock)
        self._stat_lock = threading.Lock()

    def spec(self, kind: str) -> TaskSpec:
        return self.specs.get(kind) or TaskSpec(kind=kind, title=kind)

    def _sem(self, kind: str) -> threading.BoundedSemaphore:
        with self._lock:
            if kind not in self._sems:
                self._sems[kind] = threading.BoundedSemaphore(max(1, self.spec(kind).concurrency))
            return self._sems[kind]

    def find(self, kind: str, subject: str) -> TaskRecord | None:
        tid = self._by_key.get((kind, subject))
        return self._tasks.get(tid) if tid else None

    def submit(
        self,
        kind: str,
        subject: str,
        fn: TaskFn,
        deps: list[TaskRecord] | None = None,
        cached: Callable[[], bool] | None = None,
        force: bool = False,
    ) -> TaskRecord:
        """Start (or join) ``kind`` for ``subject``. A running / queued task is joined; a finished cacheable one is
        returned as is unless ``force``; ``cached()`` says whether the answer is already in a cache (warm)."""
        with self._lock:
            cur = self.find(kind, subject)
            if cur is not None and not force:
                if cur.state in ("queued", "waiting", "running"):
                    return cur
                if cur.state == "done" and self.spec(kind).cacheable:
                    return cur
            sp = self.spec(kind)
            rec = TaskRecord(uuid.uuid4().hex[:12], kind, subject, sp.title, sp.cost, sp.why)
            rec.waiting_on = [d.id for d in deps or []]
            if rec.waiting_on:
                rec.state = "waiting"
            self._tasks[rec.id] = rec
            self._by_key[(kind, subject)] = rec.id
        self._pool.submit(self._run, rec, fn, deps or [], cached)
        return rec

    def _run(self, rec: TaskRecord, fn: TaskFn, deps: list[TaskRecord], cached: Callable[[], bool] | None) -> None:
        with self._done:
            while any(d.state in ("queued", "waiting", "running") for d in deps):
                self._done.wait(timeout=1.0)
        failed = [d for d in deps if d.state == "failed"]
        if failed:
            self._finish(rec, "failed", error=f"needs {failed[0].kind}, which failed: {failed[0].error}")
            return
        rec.waiting_on = []
        rec.state = "queued"
        with self._sem(rec.kind):
            rec.state = "running"
            rec.started_at = rec.last_activity = time.time()
            try:
                rec.cache = "warm" if cached is not None and cached() else ("cold" if cached is not None else "unknown")
            except Exception:  # a cache probe never fails the task
                rec.cache = "unknown"
            try:
                rec.result = fn(rec) or {}
                self._finish(rec, "done")
            except Exception as exc:
                self._finish(rec, "failed", error=f"{type(exc).__name__}: {str(exc)[:400]}")

    def _finish(self, rec: TaskRecord, state: State, error: str = "") -> None:
        rec.ended_at = rec.last_activity = time.time()
        if rec.started_at is None:
            rec.started_at = rec.ended_at
        if self.docs is not None and state == "done":
            self._record_timing(rec)  # before waiters wake: what they read next includes this task
        with self._done:
            rec.state, rec.error = state, error
            self._done.notify_all()

    def _record_timing(self, rec: TaskRecord) -> None:
        """Measured history per kind (last 200), the input of Light / Heavy classification."""
        with self._stat_lock:
            self._record_timing_locked(rec)

    def _record_timing_locked(self, rec: TaskRecord) -> None:
        try:
            try:
                doc = self.docs.get_raw("task_stat", rec.kind)
            except Exception:
                doc = {"kind": rec.kind, "samples": []}
            doc["samples"] = [
                *doc["samples"],
                {"ms": round(rec.elapsed_s * 1000, 1), "cache": rec.cache, "at": rec.ended_at, "subject": rec.subject},
            ][-200:]
            self.docs.put("task_stat", rec.kind, doc, summary={"kind": rec.kind, "n": len(doc["samples"])})
        except Exception as exc:  # measuring must never break the work it measures
            log.warning("task timing not recorded", kind=rec.kind, error=type(exc).__name__)

    def wait(self, rec: TaskRecord, timeout: float = 60.0) -> TaskRecord:
        end = time.time() + timeout
        with self._done:
            while rec.state in ("queued", "waiting", "running") and time.time() < end:
                self._done.wait(timeout=0.2)
        return rec

    def list(self, subject: str | None = None, active: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            recs = list(self._tasks.values())
        if subject is not None:
            recs = [r for r in recs if r.subject == subject]
        if active:
            recs = [r for r in recs if r.state in ("queued", "waiting", "running")]
        recs.sort(key=lambda r: -r.created_at)
        return [r.view() for r in recs[:limit]]

    def timings(self, kind: str) -> builtins.list[dict[str, Any]]:
        if self.docs is None:
            return []
        try:
            return list(self.docs.get_raw("task_stat", kind).get("samples", []))
        except Exception:
            return []
