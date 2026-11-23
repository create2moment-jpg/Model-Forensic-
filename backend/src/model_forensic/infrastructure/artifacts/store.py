"""Artifact download engine.

* Profiles decide WHAT to fetch (kind + size budget), so multi-GB checkpoints are never pulled
  for a FAST_DEBUG look.
* Downloads stream into ``<file>.part`` and resume from its size after interruption.
* Every completed file is SHA-256 hashed; if the backend reported a hash it is verified.
* Blobs are content-addressed under ``blobs/`` and hard-linked into the per-run view, so the same
  checkpoint shared by many runs is stored once (deduplication).
* A SQLite manifest is the local artifact index; LRU eviction enforces a retention budget.
* Parallel workers, cooperative cancellation, progress callbacks, bounded retries.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from model_forensic.application.ports import RunSource
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.run import RunRef
from model_forensic.shared.errors import (
    CancelledError,
    InsufficientDiskError,
    IntegrityError,
)
from model_forensic.shared.logging import get_logger
from model_forensic.shared.paths import safe_join
from model_forensic.shared.retry import RetryPolicy, retry

log = get_logger(__name__)


from model_forensic.domain.downloads import (  # noqa: E402 - re-exported for the store's users
    PROFILES,
    DownloadProfile,
    DownloadProgress,
    LocalArtifact,
    ProfileSpec,
    select,
)

__all__ = ["PROFILES", "ArtifactStore", "DownloadProfile", "DownloadProgress", "LocalArtifact", "ProfileSpec", "select"]


class ArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int | None = None,
        min_free_bytes: int = 1 * 1024**3,
        workers: int = 4,
        policy: RetryPolicy | None = None,
    ) -> None:
        self.root = root
        self.blobs = root / "blobs"
        self.runs = root / "runs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.runs.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.min_free_bytes = min_free_bytes
        self.workers = workers
        self.policy = policy or RetryPolicy()
        self._db_path = root / "manifest.sqlite"
        self._lock = threading.Lock()
        # one download of a file at a time: two callers fetching the same file (a tab and a background task) share
        # its ``.part`` and its index row — the second waits, then finds it cached
        self._file_locks: dict[tuple[str, str, str], threading.Lock] = {}
        self._file_locks_guard = threading.Lock()
        with self._db() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS files (backend TEXT, run_id TEXT, path TEXT, sha256 TEXT, size INTEGER, "
                "fetched_at REAL, accessed_at REAL, PRIMARY KEY(backend, run_id, path))"
            )
            c.execute("CREATE INDEX IF NOT EXISTS ix_files_sha ON files(sha256)")

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=30)

    def run_root(self, ref: RunRef) -> Path:
        """Local directory of one run's files (stage files of a pipeline live under the stage's run)."""
        return safe_join(self.runs, f"{ref.backend}/{ref.run_id}")

    def _run_dir(self, a: Artifact) -> Path:
        return safe_join(self.runs, f"{a.run.backend}/{a.run.run_id}")

    def cached(self, a: Artifact, deep: bool = False) -> LocalArtifact | None:
        """The verified local copy, or None. Always checks the file on disk against the recorded size (and the
        backend's size / hash when known); ``deep`` also re-hashes files up to 256 MB — a truncated, overwritten or
        corrupted file is never reused."""
        with self._db() as c:
            row = c.execute(
                "SELECT sha256, size FROM files WHERE backend=? AND run_id=? AND path=?",
                (a.run.backend, a.run.run_id, a.path),
            ).fetchone()
        if not row:
            return None
        local = safe_join(self._run_dir(a), a.path)
        try:
            on_disk = local.stat().st_size if local.is_file() else -1
        except OSError:
            on_disk = -1
        if (
            on_disk != row[1]
            or (a.size_bytes is not None and row[1] != a.size_bytes)
            or (a.sha256 and a.sha256 != row[0])
        ):
            return None
        if deep and row[1] <= 256 * 1024**2:
            h = hashlib.sha256()
            with local.open("rb") as fh:
                while b := fh.read(1 << 20):
                    h.update(b)
            if h.hexdigest() != row[0]:
                log.warning("cached file failed its hash check — fetching again", path=a.path)
                return None
        with self._db() as c:
            c.execute(
                "UPDATE files SET accessed_at=? WHERE backend=? AND run_id=? AND path=?",
                (time.time(), a.run.backend, a.run.run_id, a.path),
            )
        return LocalArtifact(a, local, row[0], from_cache=True)

    def partial_bytes(self, a: Artifact) -> int:
        """Bytes of an interrupted download already on disk (``<file>.part``) — resumed, never trusted as complete."""
        try:
            part = safe_join(self._run_dir(a), a.path)
        except Exception:  # an unsafe remote path is never touched
            return 0
        p = part.with_name(part.name + ".part")
        return p.stat().st_size if p.exists() else 0

    def drop_partial(self, a: Artifact) -> None:
        try:
            part = safe_join(self._run_dir(a), a.path)
        except Exception:
            return
        part.with_name(part.name + ".part").unlink(missing_ok=True)

    def free_bytes(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.root).free

    def _check_disk(self, needed: int) -> None:
        free = shutil.disk_usage(self.root).free
        if free - needed < self.min_free_bytes:
            raise InsufficientDiskError(
                f"need {needed / 1e9:.2f} GB but only {free / 1e9:.2f} GB free (keeping {self.min_free_bytes / 1e9:.1f} GB reserve)",
                details={"needed": needed, "free": free},
            )

    def fetch(
        self,
        source: RunSource,
        artifacts: Sequence[Artifact],
        *,
        cancel: threading.Event | None = None,
        on_progress: Callable[[DownloadProgress], None] | None = None,
        on_file: Callable[[Artifact, LocalArtifact | None, str | None], None] | None = None,
        on_stage: Callable[[Artifact, str, dict[str, Any]], None] | None = None,
    ) -> tuple[list[LocalArtifact], DownloadProgress]:
        """Download what is not cached (bounded workers, retries, ``.part`` resume, SHA-256). ``on_file(artifact,
        local or None, error or None)`` is called once per file as it completes, is reused, or fails; ``on_stage``
        reports what happens to one file in between — ``downloading`` (with the resume offset), ``bytes`` (a chunk
        arrived), ``validating`` (hashing) and ``retry`` (attempt, error) — for per-file observability."""
        cancel = cancel or threading.Event()
        prog = DownloadProgress(total_files=len(artifacts), total_bytes=sum(a.size_bytes or 0 for a in artifacts))
        todo, done = [], []
        for a in artifacts:
            hit = self.cached(a)
            if hit:
                done.append(hit)
                prog.cached_files += 1
                prog.done_files += 1
                if on_file:
                    on_file(a, hit, None)
            else:
                todo.append(a)
        self._check_disk(sum(a.size_bytes or 0 for a in todo))
        plock = threading.Lock()

        def one(a: Artifact) -> LocalArtifact:
            def attempt() -> LocalArtifact:
                with self._file_lock(a):
                    hit = self.cached(a)  # fetched by another caller while this one waited
                    if hit:
                        return hit
                    return self._download(source, a, cancel, prog, plock, on_progress, on_stage)

            def retried(n: int, exc: BaseException) -> None:
                if on_stage:
                    on_stage(a, "retry", {"attempt": n, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})

            return retry(attempt, self.policy, on_retry=retried)

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(one, a): a for a in todo}
            for fut in as_completed(futs):
                a = futs[fut]
                try:
                    la = fut.result()
                    done.append(la)
                    if on_file:
                        on_file(a, la, None)
                except CancelledError:
                    prog.failed.append((a.path, "cancelled"))
                    if on_file:
                        on_file(a, None, "cancelled")
                except Exception as exc:
                    log.warning("artifact download failed", path=a.path, error=type(exc).__name__)
                    prog.failed.append((a.path, f"{type(exc).__name__}: {exc}"))
                    if on_file:
                        on_file(a, None, f"{type(exc).__name__}: {exc}")
                with plock:
                    prog.done_files += 1
                if on_progress:
                    on_progress(prog)
        if cancel.is_set():
            raise CancelledError("download cancelled", details={"completed": len(done)})
        self.enforce_retention()
        return sorted(done, key=lambda la: la.artifact.path), prog

    def _file_lock(self, a: Artifact) -> threading.Lock:
        with self._file_locks_guard:
            return self._file_locks.setdefault((a.run.backend, a.run.run_id, a.path), threading.Lock())

    def _download(
        self,
        source: RunSource,
        a: Artifact,
        cancel: threading.Event,
        prog: DownloadProgress,
        plock: threading.Lock,
        on_progress: Callable[[DownloadProgress], None] | None,
        on_stage: Callable[[Artifact, str, dict[str, Any]], None] | None = None,
    ) -> LocalArtifact:
        final = safe_join(self._run_dir(a), a.path)
        final.parent.mkdir(parents=True, exist_ok=True)
        part = final.with_name(final.name + ".part")
        offset = part.stat().st_size if part.exists() else 0
        if on_stage:
            on_stage(a, "downloading", {"offset": offset})
        with part.open("ab") as fh:
            for chunk in source.open_artifact(a.run.run_id, a.path, offset=offset):
                if cancel.is_set():
                    raise CancelledError("cancelled")
                fh.write(chunk)
                with plock:
                    prog.done_bytes += len(chunk)
                if on_stage:
                    on_stage(a, "bytes", {"n": len(chunk)})
                if on_progress:
                    on_progress(prog)
        if on_stage:
            on_stage(a, "validating", {})
        h = hashlib.sha256()
        with part.open("rb") as fh:
            while b := fh.read(1 << 20):
                h.update(b)
        digest = h.hexdigest()
        size = part.stat().st_size
        if a.sha256 and a.sha256 != digest:
            part.unlink(missing_ok=True)
            raise IntegrityError(f"checksum mismatch for {a.path}")
        if a.size_bytes is not None and a.size_bytes != size:
            part.unlink(missing_ok=True)
            raise IntegrityError(f"size mismatch for {a.path}: expected {a.size_bytes}, got {size}")
        blob = self.blobs / digest[:2] / digest
        with self._lock:
            if blob.exists():
                part.unlink()
            else:
                blob.parent.mkdir(parents=True, exist_ok=True)
                part.replace(blob)
                with contextlib.suppress(OSError):  # hard-linked across runs: one edit must not corrupt the others
                    blob.chmod(0o444)
            if final.exists():
                final.unlink()
            try:
                os.link(blob, final)
            except OSError:
                shutil.copy2(blob, final)
        now = time.time()
        with self._db() as c:
            c.execute(
                "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)",
                (a.run.backend, a.run.run_id, a.path, digest, size, now, now),
            )
        return LocalArtifact(a, final, digest, from_cache=False)

    def usage_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.blobs.rglob("*") if p.is_file())

    def forget(
        self, backend: str | None = None, run_id: str | None = None, keep: Callable[[str], bool] | None = None
    ) -> tuple[int, int]:
        """Remove local copies (never anything remote): all, one workspace or one run; ``keep(path)`` spares files.
        Returns (files removed, bytes freed); a blob shared with a kept file stays."""
        files = freed = 0
        sql, args = "SELECT backend, run_id, path, sha256, size FROM files", []
        if backend:
            sql += " WHERE backend=?"
            args.append(backend)
            if run_id:
                sql += " AND run_id=?"
                args.append(run_id)
        with self._lock, self._db() as c:
            for b, rid, path, sha, size in c.execute(sql, args).fetchall():
                if keep is not None and keep(path):
                    continue
                safe_join(self.runs, f"{b}/{rid}/{path}").unlink(missing_ok=True)
                c.execute("DELETE FROM files WHERE backend=? AND run_id=? AND path=?", (b, rid, path))
                files += 1
                if not c.execute("SELECT 1 FROM files WHERE sha256=?", (sha,)).fetchone():
                    blob = self.blobs / sha[:2] / sha
                    with contextlib.suppress(OSError):
                        blob.chmod(0o644)
                    blob.unlink(missing_ok=True)
                    freed += size or 0
        return files, freed

    def enforce_retention(self) -> int:
        """Evict least-recently-accessed runs' files until under ``max_bytes``. Returns bytes freed."""
        if self.max_bytes is None:
            return 0
        freed = 0
        usage = self.usage_bytes()
        if usage <= self.max_bytes:
            return 0
        with self._db() as c:
            rows = c.execute(
                "SELECT backend, run_id, path, sha256, size FROM files ORDER BY accessed_at ASC"
            ).fetchall()
            for backend, run_id, path, sha, size in rows:
                if usage - freed <= self.max_bytes:
                    break
                local = safe_join(self.runs, f"{backend}/{run_id}/{path}")
                local.unlink(missing_ok=True)
                c.execute("DELETE FROM files WHERE backend=? AND run_id=? AND path=?", (backend, run_id, path))
                if not c.execute("SELECT 1 FROM files WHERE sha256=?", (sha,)).fetchone():
                    (self.blobs / sha[:2] / sha).unlink(missing_ok=True)
                    freed += size
        return freed
