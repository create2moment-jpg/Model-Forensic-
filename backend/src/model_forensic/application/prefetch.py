"""Artifact prefetch tasks: plan first, then download — observable, stoppable, resumable, integrity-safe.

Stage 1 (**plan**) lists every file of the run (and its pipeline stages) with its size, pre-selects a profile, and
says what will be reused (verified cache), what resumes from a partial download, how much is left to fetch, how long
that should take at the throughput seen before, whether the disk has room, and the risks (unknown sizes, very large
files, not enough space). The person can change the selection. Stage 2 (**run**) downloads the selection with the
store's bounded workers, retries, ``.part`` resume and SHA-256 checks, updating the persisted manifest file by file.

States: ``planned`` → ``running`` → ``completed`` | ``failed``; ``running`` → ``stopping`` → ``stopped`` (resumable:
completed files and partial downloads are kept); ``cancelled`` (not resumable: partial downloads are removed).
A task that was running when the server stopped comes back ``stopped`` (resumable). Resume re-validates every
completed file against the cache (size + hash) and downloads only what is missing or invalid.

Observability (what is it doing, how much is done, what blocks it, what happens next):

* **phase** — ``planned → manifesting → awaiting_selection → queued → downloading → validating → completed``, plus
  ``stopping``, ``stopped`` (resumable), ``cancelled``, ``failed``; time spent per phase is recorded.
* **per-file state** — ``pending · queued · downloading · validating · done · reused · partial · failed ·
  cancelled``, with bytes, attempts and timings per file.
* **heartbeat** — the worker beats every second while it runs; a stale heartbeat means it is not alive.
* **structured events** (``kind``: phase, file, retry, stall, recovered, info, error) with timestamps.
* **stall detection** — no byte for ``stall_after_s`` while downloading raises a stall event naming the files.
* **diagnosis** — the slow / blocked causes the evidence supports (stalled stream, retries, low throughput against
  earlier downloads, per-file latency on many small files, a single worker, disk space, a slow listing).
* **honest progress** — a fraction only when every selected size is known; otherwise counts and an indeterminate
  bar, never an invented percentage.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.downloads import DownloadProfile, LocalArtifact, select
from model_forensic.shared.errors import NotFoundError, ValidationError
from model_forensic.shared.logging import get_logger

log = get_logger(__name__)
KIND = "prefetch_task"
State = Literal["planned", "running", "stopping", "stopped", "completed", "failed", "cancelled"]
ItemState = Literal[
    "pending", "queued", "downloading", "validating", "partial", "reused", "done", "failed", "cancelled"
]
PHASES: tuple[str, ...] = (
    "planned", "manifesting", "awaiting_selection", "queued", "downloading", "validating", "completed",
    "stopping", "stopped", "cancelled", "failed",
)  # fmt: skip
STALL_AFTER_S = 60.0
HEARTBEAT_STALE_S = 10.0
RESUMABLE: tuple[str, ...] = ("planned", "stopped", "failed")
LARGE = 1024**3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_of(a: Artifact) -> str:
    return f"{a.run}::{a.path}"


@dataclass
class Item:
    key: str
    run: str
    stage: str | None
    path: str
    kind: str
    size: int | None
    selected: bool
    state: ItemState = "pending"
    partial_bytes: int = 0
    sha256: str = ""
    local_path: str = ""
    error: str = ""
    bytes_done: int = 0  # this session
    attempts: int = 0
    started_at: float | None = None
    ended_at: float | None = None


@dataclass
class Task:
    id: str
    run: str
    title: str
    profile: str
    stages: list[str]
    state: State = "planned"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: str | None = None
    elapsed_s: float = 0.0  # time spent running, summed over sessions
    done_bytes_session: int = 0
    throughput_bps: float = 0.0
    workers: int = 0
    note: str = ""
    error: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    phase: str = "planned"
    phase_since: float = field(default_factory=time.time)
    phase_times: dict[str, float] = field(default_factory=dict)
    last_heartbeat: float = 0.0
    last_progress_at: float = 0.0
    retries: int = 0
    stalled: bool = False
    stall_after_s: float = STALL_AFTER_S

    def selected(self) -> list[Item]:
        return [i for i in self.items if i.selected]

    def summary(self, free_bytes: int | None = None, bps_hint: float | None = None) -> dict[str, Any]:
        sel = self.selected()
        reuse = [i for i in sel if i.state in ("reused", "done")]
        todo = [i for i in sel if i.state not in ("reused", "done")]
        known_todo = sum(max(0, (i.size or 0) - i.partial_bytes) for i in todo)
        bps = self.throughput_bps or bps_hint or 0.0
        risks: list[str] = []
        unknown = sum(1 for i in todo if i.size is None)
        if unknown:
            risks.append(f"{unknown} file(s) without a known size — the total may be larger")
        big = [i for i in todo if (i.size or 0) >= LARGE]
        if big:
            risks.append(f"{len(big)} file(s) over 1 GB (largest {max(i.size or 0 for i in big) / 1e9:.1f} GB)")
        if free_bytes is not None and known_todo > free_bytes:
            risks.append(f"needs {known_todo / 1e9:.1f} GB but only {free_bytes / 1e9:.1f} GB are free")
        sizes_known = all(i.size is not None for i in sel)
        total_b = sum(i.size or 0 for i in sel)
        done_b = sum(i.size or 0 for i in reuse) + sum(min(i.partial_bytes + i.bytes_done, i.size or 0) for i in todo)
        counts: dict[str, int] = {}
        for i in sel:
            counts[i.state] = counts.get(i.state, 0) + 1
        now = time.time()
        live = self.state in ("running", "stopping")
        return {
            "id": self.id,
            "run": self.run,
            "title": self.title,
            "profile": self.profile,
            "state": self.state,
            "phase": self.phase,
            "phase_elapsed_s": round(now - self.phase_since, 1),
            "phase_times": {k: round(v, 2) for k, v in self.phase_times.items()},
            "heartbeat_age_s": round(now - self.last_heartbeat, 1) if live and self.last_heartbeat else None,
            "alive": (now - self.last_heartbeat) < HEARTBEAT_STALE_S if live and self.last_heartbeat else None,
            "stalled": self.stalled,
            "retries": self.retries,
            "item_states": counts,
            # a fraction only when every selected size is known — otherwise counts and an indeterminate bar
            "progress": {
                "determinate": sizes_known and total_b > 0,
                "fraction": round(done_b / total_b, 4) if sizes_known and total_b > 0 else None,
                "files_done": len(reuse),
                "files_selected": len(sel),
                "bytes_done": done_b,
                "bytes_total": total_b if sizes_known else None,
            },
            "next": _next_step(self, len(todo)),
            "resumable": self.state in RESUMABLE and bool(todo),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "files_total": len(self.items),
            "files_selected": len(sel),
            "files_reused": sum(1 for i in sel if i.state == "reused"),
            "files_done": sum(1 for i in sel if i.state == "done"),
            "files_partial": sum(1 for i in todo if i.partial_bytes > 0),
            "files_failed": sum(1 for i in sel if i.state == "failed"),
            "files_remaining": len(todo),
            "bytes_selected": sum(i.size or 0 for i in sel),
            "bytes_reused": sum(i.size or 0 for i in reuse),
            "bytes_remaining": known_todo,
            "unknown_sizes": unknown,
            "throughput_bps": round(self.throughput_bps),
            "eta_s": round(known_todo / bps) if bps > 0 and known_todo else None,
            "eta_from": "this task" if self.throughput_bps else ("earlier downloads" if bps_hint else None),
            "elapsed_s": round(self.elapsed_s, 1),
            "workers": self.workers,
            "free_bytes": free_bytes,
            "risks": risks,
            "note": self.note,
            "error": self.error,
        }


def _next_step(t: Task, remaining: int) -> str:
    """What will happen next — in words, from the state."""
    return {
        "planned": "the file list is being prepared",
        "manifesting": "listing the files and re-validating the cache; downloads start next",
        "awaiting_selection": f"review the selection ({remaining} file(s) to fetch) and press Start",
        "queued": f"{remaining} file(s) wait for a free worker",
        "downloading": f"{remaining} file(s) left; each is hashed and checked before it counts as done",
        "validating": "checking the finished files; the task completes next",
        "stopping": "finishing the chunk in flight, then it stops (partial files are kept)",
        "stopped": f"resume to fetch the {remaining} remaining file(s) — finished and partial files are kept",
        "failed": f"resume to retry the {remaining} file(s) that did not finish",
        "cancelled": "nothing — start a new plan to fetch these files",
        "completed": "nothing — every selected file is in the cache",
    }.get(t.phase, "")


def diagnose(t: Task, hint_bps: float | None = None, free_bytes: int | None = None) -> list[dict[str, Any]]:
    """Why is it slow or blocked? Only causes the recorded evidence supports, each with its evidence and a fix."""
    out: list[dict[str, Any]] = []
    now = time.time()
    sel = t.selected()
    todo = [i for i in sel if i.state not in ("reused", "done")]
    running = t.state in ("running", "stopping")

    def add(cause: str, severity: str, evidence: str, fix: str) -> None:
        out.append({"cause": cause, "severity": severity, "evidence": evidence, "fix": fix})

    if running and t.last_heartbeat and now - t.last_heartbeat > HEARTBEAT_STALE_S:
        add("worker_not_alive", "critical", f"no heartbeat for {now - t.last_heartbeat:.0f} s",
            "the worker thread is gone — stop and resume the task")  # fmt: skip
    if t.stalled:
        busy = [i for i in sel if i.state == "downloading"]
        names = ", ".join(f"{i.path} ({i.partial_bytes + i.bytes_done} B)" for i in busy[:3]) or "no file in flight"
        add("stalled", "critical", f"no byte for {now - (t.last_progress_at or now):.0f} s while downloading: {names}",
            "the backend stream stopped answering — stop and resume (the partial file is kept), or check the network")  # fmt: skip
    if t.retries:
        last = next((e for e in reversed(t.events) if e.get("kind") == "retry"), None)
        add("backend_transient", "warning", f"{t.retries} retried request(s)" + (f"; last: {last['text']}" if last else ""),
            "transient backend or network errors — retries are automatic; many retries mean throttling or a weak link")  # fmt: skip
    if (
        t.throughput_bps
        and hint_bps
        and t.throughput_bps < 0.3 * hint_bps
        and (t.elapsed_s > 10 or (t.phase == "downloading" and now - t.phase_since > 10))
    ):
        add("low_throughput", "warning",
            f"{t.throughput_bps / 1e6:.2f} MB/s now vs {hint_bps / 1e6:.2f} MB/s in earlier downloads",
            "the network or the backend is slower than usual — check VPN / proxy, or prefetch outside busy hours")  # fmt: skip
    finished = [i for i in sel if i.state == "done" and i.started_at and i.ended_at]
    if len(finished) >= 5:
        avg_b = sum(i.size or 0 for i in finished) / len(finished)
        avg_s = sum((i.ended_at or 0) - (i.started_at or 0) for i in finished) / len(finished)
        if avg_b < 1e6 and avg_s > 0.3:
            add("per_file_latency", "info",
                f"{len(finished)} small files (avg {avg_b / 1e3:.0f} kB) took {avg_s:.2f} s each — request latency dominates",
                "more parallel workers help (Settings → Storage → workers); bytes are not the bottleneck")  # fmt: skip
    if running and t.workers == 1 and len(todo) > 1:
        add("single_worker", "info", f"1 worker for {len(todo)} remaining files",
            "raise the download workers in Settings → Storage")  # fmt: skip
    need = sum(max(0, (i.size or 0) - i.partial_bytes - i.bytes_done) for i in todo)
    if free_bytes is not None and need > free_bytes:
        add("disk_space", "critical", f"needs {need / 1e9:.1f} GB, {free_bytes / 1e9:.1f} GB free",
            "free space or point the artifact cache elsewhere (Settings → Storage)")  # fmt: skip
    if t.phase == "manifesting" and now - t.phase_since > 10:
        add("slow_listing", "warning", f"listing the files for {now - t.phase_since:.0f} s",
            "the backend is slow to list the run's files — large output folders take longer; it continues on its own")  # fmt: skip
    return out


def _task_from(doc: dict[str, Any]) -> Task:
    items = [Item(**i) for i in doc.get("items", [])]
    return Task(**{**{k: v for k, v in doc.items() if k != "items"}, "items": items})


class PrefetchManager:
    """Owns prefetch tasks (persisted in the document store) and their worker threads."""

    def __init__(self, svc: Any, docs: Any) -> None:
        self.svc, self.docs = svc, docs
        self._lock = threading.RLock()
        self._live: dict[str, tuple[Task, threading.Event, threading.Thread]] = {}
        self._recover()

    # ------------------------------------------------------------------------------------------ persistence
    def _save(self, t: Task) -> None:
        t.updated_at = _now()
        self.docs.put(KIND, t.id, asdict(t), summary=t.summary())

    def _event(self, t: Task, text: str, kind: str = "info", **data: Any) -> None:
        """A structured event (kind: phase, file, retry, stall, recovered, info, error)."""
        t.events = [*t.events, {"at": _now(), "kind": kind, "text": text, **data}][-300:]

    def _phase(self, t: Task, phase: str) -> None:
        if phase == t.phase:
            return
        now = time.time()
        t.phase_times[t.phase] = t.phase_times.get(t.phase, 0.0) + (now - t.phase_since)
        self._event(t, f"{t.phase} → {phase}", "phase", phase=phase, after_s=round(now - t.phase_since, 2))
        t.phase, t.phase_since = phase, now

    def _recover(self) -> None:
        """Tasks that were running when the server stopped are resumable, not lost."""
        for s in self.docs.list_summaries(KIND, limit=100_000)[0]:
            if s.get("state") in ("running", "stopping"):
                t = _task_from(self.docs.get_raw(KIND, s["id"]))
                t.state = "stopped"
                self._phase(t, "stopped")
                for i in t.items:
                    if i.state in ("queued", "downloading", "validating"):
                        i.state = "partial" if i.partial_bytes else "pending"
                t.note = "interrupted by a server restart — resume to continue"
                self._event(t, t.note)
                self._save(t)

    def get(self, tid: str) -> Task:
        live = self._live.get(tid)
        if live:
            return live[0]
        try:
            return _task_from(self.docs.get_raw(KIND, tid))
        except NotFoundError:
            raise NotFoundError(f"prefetch task {tid!r} not found") from None

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        items, _ = self.docs.list_summaries(KIND, limit=limit)
        live = {tid: t.summary(self._free()) for tid, (t, _e, _th) in self._live.items()}
        out = [live.get(s["id"], s) for s in items]
        order = {"running": 0, "stopping": 0, "planned": 1, "stopped": 2, "failed": 3, "completed": 4, "cancelled": 5}
        return sorted(out, key=lambda s: (order.get(s["state"], 9), s.get("updated_at", "")), reverse=False)

    def _free(self) -> int | None:
        try:
            return int(self.svc.artifact_store.free_bytes())
        except OSError:
            return None

    def _bps_hint(self) -> float | None:
        """Throughput of earlier tasks (for an honest time estimate before the first byte)."""
        rates = [s.get("throughput_bps") or 0 for s in self.docs.list_summaries(KIND, limit=20)[0]]
        rates = [r for r in rates if r > 0]
        return float(sorted(rates)[len(rates) // 2]) if rates else None

    def view(self, tid: str) -> dict[str, Any]:
        t = self.get(tid)
        free, hint = self._free(), self._bps_hint()
        try:
            root = str(self.svc.artifact_store.run_root(self.svc.ref(t.run)))
        except Exception:
            root = ""

        def rel(i: Any) -> str:
            try:
                return self.svc.rel_path_of(t.run, i.run, i.stage, i.path) if i.local_path else ""
            except Exception:
                return ""

        return {
            **t.summary(free, hint),
            "local_root": root,  # the run's folder: every item's rel_path is under it
            "items": [{**asdict(i), "rel_path": rel(i)} for i in t.items],
            "events": t.events,
            "diagnosis": diagnose(t, hint, free),
            "in_flight": [
                {
                    "path": i.path,
                    "stage": i.stage,
                    "bytes": i.partial_bytes + i.bytes_done,
                    "size": i.size,
                    "state": i.state,
                    "attempts": i.attempts,
                    "for_s": round(time.time() - (i.started_at or time.time()), 1),
                }
                for i in t.items
                if i.state in ("downloading", "validating")
            ],
        }

    def events(self, tid: str, after: int = 0) -> dict[str, Any]:
        """Structured events since index ``after`` (for a live log)."""
        ev = self.get(tid).events
        return {"events": ev[after:], "next": len(ev)}

    # ------------------------------------------------------------------------------------------ stage 1: plan
    def _refresh_items(self, t: Task, artifacts: Sequence[Artifact]) -> None:
        store = self.svc.artifact_store
        by = {key_of(a): a for a in artifacts}
        for it in t.items:
            a = by.get(it.key)
            if a is None:
                continue
            hit = store.cached(a, deep=True)  # plan / resume: re-validate, never trust a file blindly
            if hit is not None:
                it.state = "reused" if it.state != "done" else "done"
                it.sha256, it.local_path, it.partial_bytes, it.error = hit.sha256, str(hit.local_path), 0, ""
            else:
                it.partial_bytes = store.partial_bytes(a)
                it.state = "partial" if it.partial_bytes else ("failed" if it.state == "failed" else "pending")
                it.local_path = ""

    def plan(
        self,
        run: str,
        profile: str = DownloadProfile.FAST_DEBUG.value,
        stages: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        r = self.svc.ref(run)
        prof = DownloadProfile(profile)
        if not paths:  # the same plan asked for again: reopen the unstarted one instead of piling up duplicates
            for s in self.docs.list_summaries(KIND, where={"run": str(r), "state": "planned"}, limit=50)[0]:
                t = self.get(s["id"])
                if t.profile == prof.value and sorted(t.stages) == sorted(stages or []):
                    self._refresh_items(t, self.svc.artifacts(r))
                    self._save(t)
                    return self.view(t.id)
        arts = self.svc.artifacts(r)
        if stages:
            arts = [a for a in arts if (a.stage or "") in set(stages)]
        chosen = {key_of(a) for a in (self.svc.download_plan(r, prof, stages, paths) if paths else select(arts, prof))}
        run_obj = self.svc.get_run(r)
        t = Task(
            id=uuid.uuid4().hex[:12],
            run=str(r),
            title=f"{run_obj.display_name or run_obj.name} · {len(paths) if paths else prof.value}"
            + (" file(s)" if paths else ""),
            profile=prof.value if not paths else "custom",
            stages=list(stages or []),
            items=[
                Item(key_of(a), str(a.run), a.stage, a.path, a.kind.value, a.size_bytes, key_of(a) in chosen)
                for a in sorted(arts, key=lambda a: (a.stage or "", a.path))
            ],
        )
        self._phase(t, "manifesting")
        t0 = time.time()
        self._refresh_items(t, arts)
        self._event(
            t, f"manifest: {len(t.items)} files listed and checked against the cache in {time.time() - t0:.2f} s"
        )
        self._phase(t, "awaiting_selection")
        self._event(t, f"planned: {len(t.selected())} of {len(t.items)} files selected")
        self._save(t)
        return self.view(t.id)

    def select(
        self,
        tid: str,
        keys: Sequence[str] | None = None,
        selected: bool = True,
        only: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Change the selection (planned / stopped / failed tasks). ``only`` replaces it; else ``keys`` are set."""
        with self._lock:
            t = self.get(tid)
            if t.state not in RESUMABLE:
                raise ValidationError(f"the selection of a {t.state} task cannot change")
            if only is not None:
                want = set(only)
                for it in t.items:
                    it.selected = it.key in want
            else:
                want = set(keys or [])
                for it in t.items:
                    if it.key in want:
                        it.selected = selected
            self._save(t)
        return self.view(tid)

    # ------------------------------------------------------------------------------------------ stage 2: run
    def start(self, tid: str) -> dict[str, Any]:
        """Start (planned) or resume (stopped / failed) — re-validates finished files, fetches only what is missing."""
        with self._lock:
            if tid in self._live:
                return self.view(tid)
            t = self.get(tid)
            if t.state not in RESUMABLE:
                raise ValidationError(f"a {t.state} task cannot be started")
            resumed = t.state != "planned"
            t.state, t.error, t.note, t.stalled, t.retries = "running", "", "", False, 0
            t.started_at = _now()
            t.done_bytes_session = 0
            t.last_heartbeat = t.last_progress_at = time.time()
            for i in t.items:
                i.bytes_done = 0
            self._event(t, "resumed" if resumed else "started")
            self._phase(t, "manifesting")  # the listing runs in the worker: start answers at once
            self._save(t)
            stop = threading.Event()
            th = threading.Thread(target=self._work, args=(t, None, stop), daemon=True, name=f"prefetch-{tid}")
            self._live[tid] = (t, stop, th)
            th.start()
        return self.view(tid)

    def _heartbeat(self, t: Task, stop: threading.Event, done: threading.Event) -> None:
        """Beat every second while the worker runs; raise / clear a stall when no byte arrives for a while."""
        last_save = time.monotonic()
        while not done.wait(1.0):
            now = time.time()
            t.last_heartbeat = now
            quiet = now - (t.last_progress_at or now)
            if t.phase == "downloading" and not stop.is_set() and quiet > t.stall_after_s and not t.stalled:
                t.stalled = True
                busy = [i.path for i in t.items if i.state == "downloading"]
                self._event(t, f"stalled: no byte for {quiet:.0f} s ({', '.join(busy[:3]) or 'no file in flight'})",
                            "stall", files=busy[:10])  # fmt: skip
            if time.monotonic() - last_save > 2.0:
                last_save = time.monotonic()
                with self._lock:
                    if t.id in self._live:
                        self._save(t)

    def _work(self, t: Task, arts: dict[str, Artifact] | None, stop: threading.Event) -> None:
        done_ev = threading.Event()
        threading.Thread(target=self._heartbeat, args=(t, stop, done_ev), daemon=True, name=f"hb-{t.id}").start()
        t0 = time.monotonic()
        try:
            self._work_inner(t, arts, stop, t0)
        finally:
            done_ev.set()

    def _work_inner(self, t: Task, arts: dict[str, Artifact] | None, stop: threading.Event, t0: float) -> None:
        if arts is None:
            try:
                lt = time.time()
                arts = {key_of(a): a for a in self.svc.artifacts(self.svc.ref(t.run))}
                self._refresh_items(t, list(arts.values()))
                self._event(t, f"manifest: {len(arts)} files listed and re-validated in {time.time() - lt:.2f} s")
            except Exception as exc:
                t.state, t.error = "failed", f"listing the files failed: {type(exc).__name__}: {str(exc)[:300]}"
                self._event(t, t.error, "error")
                self._phase(t, "failed")
                t.workers = 0
                with self._lock:
                    self._live.pop(t.id, None)
                    self._save(t)
                return
        t.workers = int(getattr(self.svc.artifact_store, "workers", 1))
        todo = [arts[i.key] for i in t.selected() if i.state not in ("reused", "done") and i.key in arts]
        missing = [i for i in t.selected() if i.key not in arts]
        for i in missing:
            i.state, i.error = "failed", "no longer listed by the backend"
        items = {i.key: i for i in t.items}
        for a in todo:
            items[key_of(a)].state = "queued"
        self._event(
            t,
            f"{sum(1 for i in t.selected() if i.state in ('reused', 'done'))} file(s) reused, {len(todo)} to fetch "
            f"with {t.workers} worker(s)",
        )
        self._phase(t, "queued" if todo else "validating")
        last_save = [0.0]

        def on_stage(a: Artifact, what: str, info: dict[str, Any]) -> None:
            it = items.get(key_of(a))
            if it is None:
                return
            now = time.time()
            if what == "bytes":
                it.bytes_done += int(info.get("n", 0))
                t.last_progress_at = now
                if t.stalled:
                    t.stalled = False
                    self._event(t, f"recovered: bytes flow again ({it.path})", "recovered")
                return
            if what == "downloading":
                it.state, it.attempts = "downloading", it.attempts + 1
                it.started_at = it.started_at or now
                it.partial_bytes, it.bytes_done = int(info.get("offset", 0)), 0
                t.last_progress_at = now
                if t.phase == "queued":
                    self._phase(t, "downloading")
                self._event(t, f"downloading {it.path}" + (f" (resuming at {it.partial_bytes} B)" if it.partial_bytes else ""),
                            "file", path=it.path, state="downloading")  # fmt: skip
            elif what == "validating":
                it.state = "validating"
            elif what == "retry":
                t.retries += 1
                self._event(t, f"retry {info.get('attempt')} of {it.path}: {info.get('error')}", "retry", path=it.path)

        def on_file(a: Artifact, la: LocalArtifact | None, err: str | None) -> None:
            it = items.get(key_of(a))
            if it is None:
                return
            it.ended_at = time.time()
            if la is not None:
                it.state = "reused" if la.from_cache else "done"
                it.sha256, it.local_path, it.partial_bytes, it.error = la.sha256, str(la.local_path), 0, ""
                if not la.from_cache:
                    self._event(t, f"done {it.path}", "file", path=it.path, state="done")
            elif err == "cancelled":
                it.partial_bytes, it.bytes_done = self.svc.artifact_store.partial_bytes(a), 0
                it.state = "partial" if it.partial_bytes else "pending"
            else:
                it.state, it.error = "failed", (err or "failed")[:300]
                self._event(t, f"failed {it.path}: {it.error}", "error", path=it.path)
            if time.monotonic() - last_save[0] > 1.0:
                last_save[0] = time.monotonic()
                self._save(t)

        def on_progress(pr: Any) -> None:
            t.done_bytes_session = pr.done_bytes
            dt = time.monotonic() - t0
            t.throughput_bps = pr.done_bytes / dt if dt > 0 else 0.0

        try:
            if todo:
                self.svc.artifact_store.fetch(
                    self.svc.source(self.svc.ref(t.run)),
                    todo,
                    cancel=stop,
                    on_progress=on_progress,
                    on_file=on_file,
                    on_stage=on_stage,
                )
            self._phase(t, "validating")
            store = self.svc.artifact_store
            for i in t.selected():  # the final check: every finished file is really in the cache
                if i.state in ("done", "reused") and i.key in arts and store.cached(arts[i.key]) is None:
                    i.state, i.error = "failed", "missing from the cache after download"
            failed = [i for i in t.selected() if i.state == "failed"]
            t.state = "failed" if failed else "completed"
            t.error = f"{len(failed)} file(s) failed — resume to retry them" if failed else ""
            self._event(t, t.error or "completed", "error" if failed else "info")
            self._phase(t, t.state)
        except Exception as exc:
            if stop.is_set():
                cancelled = t.state == "cancelled" or t.note == "cancel"
                t.state = "cancelled" if cancelled else "stopped"
                if cancelled:
                    for i in t.selected():
                        if i.state in ("partial", "pending") and i.key in arts:
                            self.svc.artifact_store.drop_partial(arts[i.key])
                            i.partial_bytes, i.state = 0, "pending"
                    t.note = "cancelled — partial downloads removed; finished files stay in the cache"
                else:
                    t.note = "stopped — finished files and partial downloads are kept; resume to continue"
                for i in t.selected():
                    if i.state in ("queued", "downloading", "validating"):
                        i.state = "cancelled" if cancelled else ("partial" if i.partial_bytes else "pending")
                self._event(t, t.note)
                self._phase(t, t.state)
            else:
                t.state, t.error = "failed", f"{type(exc).__name__}: {str(exc)[:400]}"
                for i in t.selected():
                    if i.state in ("queued", "downloading", "validating"):
                        i.state = "partial" if i.partial_bytes else "pending"
                self._event(t, f"failed: {t.error}", "error")
                self._phase(t, "failed")
        finally:
            t.elapsed_s += time.monotonic() - t0
            t.workers = 0
            with self._lock:
                self._live.pop(t.id, None)
                self._save(t)
            try:
                got = [
                    LocalArtifact(arts[i.key], Path(i.local_path), i.sha256, i.state == "reused")
                    for i in t.items
                    if i.state in ("done", "reused") and i.local_path and i.key in arts
                ]
                self.svc.link_stages(self.svc.ref(t.run), got)  # every file under the run's folder: stages/<stage>/
                self.svc.write_manifest(self.svc.ref(t.run), got)
            except Exception as exc:  # the provenance manifest is best effort
                log.warning("download manifest not written", error=type(exc).__name__)

    def stop(self, tid: str, cancel: bool = False) -> dict[str, Any]:
        """Stop (resumable: keeps finished and partial files) or cancel (removes partial downloads)."""
        with self._lock:
            live = self._live.get(tid)
            if live is None:
                t = self.get(tid)
                if cancel and t.state in RESUMABLE:
                    t.state, t.note = "cancelled", "cancelled before it ran"
                    self._event(t, t.note)
                    self._phase(t, "cancelled")
                    self._save(t)
                return self.view(tid)
            t, ev, _th = live
            t.state = "cancelled" if cancel else "stopping"
            t.note = "cancel" if cancel else ""
            self._phase(t, "stopping")
            ev.set()
        return self.view(tid)

    def wait(self, tid: str, timeout: float = 30.0) -> None:
        live = self._live.get(tid)
        if live:
            live[2].join(timeout)

    def delete(self, tid: str) -> None:
        if tid in self._live:
            raise ValidationError("stop the task before deleting it")
        self.get(tid)
        self.docs.delete(KIND, tid)
