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
from model_forensic.infrastructure.artifacts.store import DownloadProfile, LocalArtifact, select
from model_forensic.shared.errors import NotFoundError, ValidationError
from model_forensic.shared.logging import get_logger

log = get_logger(__name__)
KIND = "prefetch_task"
State = Literal["planned", "running", "stopping", "stopped", "completed", "failed", "cancelled"]
ItemState = Literal["pending", "partial", "reused", "done", "failed"]
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
    events: list[dict[str, str]] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)

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
        return {
            "id": self.id,
            "run": self.run,
            "title": self.title,
            "profile": self.profile,
            "state": self.state,
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

    def _event(self, t: Task, text: str) -> None:
        t.events = [*t.events, {"at": _now(), "text": text}][-50:]

    def _recover(self) -> None:
        """Tasks that were running when the server stopped are resumable, not lost."""
        for s in self.docs.list_summaries(KIND, limit=100_000)[0]:
            if s.get("state") in ("running", "stopping"):
                t = _task_from(self.docs.get_raw(KIND, s["id"]))
                t.state = "stopped"
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
        return {**t.summary(self._free(), self._bps_hint()), "items": [asdict(i) for i in t.items], "events": t.events}

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
        self._refresh_items(t, arts)
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
            arts = {key_of(a): a for a in self.svc.artifacts(self.svc.ref(t.run))}
            self._refresh_items(t, list(arts.values()))
            resumed = t.state != "planned"
            t.state, t.error, t.note = "running", "", ""
            t.started_at = _now()
            t.done_bytes_session = 0
            t.workers = int(getattr(self.svc.artifact_store, "workers", 1))
            self._event(
                t,
                ("resumed" if resumed else "started")
                + f": {sum(1 for i in t.selected() if i.state in ('reused', 'done'))} file(s) reused, "
                f"{sum(1 for i in t.selected() if i.state not in ('reused', 'done'))} to fetch",
            )
            self._save(t)
            stop = threading.Event()
            th = threading.Thread(target=self._work, args=(t, arts, stop), daemon=True, name=f"prefetch-{tid}")
            self._live[tid] = (t, stop, th)
            th.start()
        return self.view(tid)

    def _work(self, t: Task, arts: dict[str, Artifact], stop: threading.Event) -> None:
        todo = [arts[i.key] for i in t.selected() if i.state not in ("reused", "done") and i.key in arts]
        missing = [i for i in t.selected() if i.key not in arts]
        for i in missing:
            i.state, i.error = "failed", "no longer listed by the backend"
        items = {i.key: i for i in t.items}
        t0 = time.monotonic()
        last_save = [0.0]

        def on_file(a: Artifact, la: LocalArtifact | None, err: str | None) -> None:
            it = items.get(key_of(a))
            if it is None:
                return
            if la is not None:
                it.state = "reused" if la.from_cache else "done"
                it.sha256, it.local_path, it.partial_bytes, it.error = la.sha256, str(la.local_path), 0, ""
            elif err == "cancelled":
                it.partial_bytes = self.svc.artifact_store.partial_bytes(a)
                it.state = "partial" if it.partial_bytes else "pending"
            else:
                it.state, it.error = "failed", (err or "failed")[:300]
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
                    self.svc.source(self.svc.ref(t.run)), todo, cancel=stop, on_progress=on_progress, on_file=on_file
                )
            failed = [i for i in t.selected() if i.state == "failed"]
            t.state = "failed" if failed else "completed"
            t.error = f"{len(failed)} file(s) failed — resume to retry them" if failed else ""
            self._event(t, t.error or "completed")
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
                self._event(t, t.note)
            else:
                t.state, t.error = "failed", f"{type(exc).__name__}: {str(exc)[:400]}"
                self._event(t, f"failed: {t.error}")
        finally:
            t.elapsed_s += time.monotonic() - t0
            t.workers = 0
            with self._lock:
                self._live.pop(t.id, None)
                self._save(t)
            try:
                self.svc.write_manifest(
                    self.svc.ref(t.run),
                    [
                        LocalArtifact(arts[i.key], Path(i.local_path), i.sha256, i.state == "reused")
                        for i in t.items
                        if i.state in ("done", "reused") and i.local_path and i.key in arts
                    ],
                )
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
                    self._save(t)
                return self.view(tid)
            t, ev, _th = live
            t.state = "cancelled" if cancel else "stopping"
            t.note = "cancel" if cancel else ""
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
