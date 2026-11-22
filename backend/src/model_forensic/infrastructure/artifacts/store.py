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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from model_forensic.application.ports import RunSource
from model_forensic.domain.artifacts import Artifact, ArtifactKind
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


class DownloadProfile(str, Enum):
    FAST_DEBUG = "fast_debug"
    FULL_FORENSIC = "full_forensic"
    CHECKPOINT_ONLY = "checkpoint_only"
    EVALUATION_ONLY = "evaluation_only"
    # Presets of the v1 tool (Azure pipeline layout)
    LOGS = "logs"
    CONFIGS = "configs"
    EVAL_OUTPUTS = "eval_outputs"
    EVAL_JSON = "eval_json"
    EVAL_PARQUET = "eval_parquet"
    METRICS_CSV = "metrics_csv"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ProfileSpec:
    kinds: frozenset[ArtifactKind]
    max_file_bytes: int | None
    include_globs: tuple[str, ...] = ()
    label: str = ""
    help: str = ""
    match: Callable[[str], bool] | None = None  # predicate on the path (``stage_output/`` stripped); replaces kinds


def _rel(path: str) -> str:
    return path.removeprefix("stage_output/")


def _is_eval_path(path: str) -> bool:
    p = _rel(path)
    return p.startswith(("evaluation/", "summaries/", "visualizations/")) or p == "pipeline_info.json"


PROFILES: dict[DownloadProfile, ProfileSpec] = {
    DownloadProfile.FAST_DEBUG: ProfileSpec(
        frozenset(
            {
                ArtifactKind.LOG,
                ArtifactKind.METRICS,
                ArtifactKind.CONFIG,
                ArtifactKind.EVALUATION,
                ArtifactKind.ENVIRONMENT,
            }
        ),
        max_file_bytes=50 * 1024 * 1024,
        label="Fast debug",
        help="Logs, metrics, configs and small evaluation files (≤ 50 MB each)",
    ),
    DownloadProfile.LOGS: ProfileSpec(
        frozenset(),
        None,
        label="Logs",
        help="stdout/stderr and user logs",
        match=lambda p: _rel(p).startswith(("user_logs/", "logs/", "system_logs/", "azureml-logs/")),
    ),
    DownloadProfile.CONFIGS: ProfileSpec(
        frozenset(),
        None,
        label="Configs",
        help="composed_config.yaml and other run configs",
        match=lambda p: _rel(p).rsplit("/", 1)[-1] in {"composed_config.yaml", "config.yaml", "config.json"},
    ),
    DownloadProfile.EVAL_OUTPUTS: ProfileSpec(
        frozenset(),
        2 * 1024**3,
        label="Evaluation outputs",
        help="evaluation/, summaries/, visualizations/",
        match=_is_eval_path,
    ),
    DownloadProfile.EVAL_JSON: ProfileSpec(
        frozenset(),
        None,
        label="Evaluation JSON",
        help="Only the JSON files of the evaluation outputs",
        match=lambda p: _is_eval_path(p) and p.lower().endswith(".json"),
    ),
    DownloadProfile.EVAL_PARQUET: ProfileSpec(
        frozenset(),
        None,
        label="Per-sequence parquet",
        help="Per-sequence evaluation tables (*.parquet)",
        match=lambda p: p.lower().endswith(".parquet"),
    ),
    DownloadProfile.METRICS_CSV: ProfileSpec(
        frozenset(),
        None,
        label="Lightning metrics.csv",
        help="summaries/**/metrics.csv (losses, LR)",
        match=lambda p: _rel(p).lower().startswith("summaries/") and p.lower().endswith("metrics.csv"),
    ),
    DownloadProfile.EVALUATION_ONLY: ProfileSpec(
        frozenset({ArtifactKind.EVALUATION, ArtifactKind.PREDICTIONS, ArtifactKind.METRICS}),
        max_file_bytes=2 * 1024**3,
        label="Evaluation + predictions",
        help="Evaluation results, predictions and metric files",
    ),
    DownloadProfile.CHECKPOINT_ONLY: ProfileSpec(
        frozenset({ArtifactKind.CHECKPOINT, ArtifactKind.MODEL}),
        max_file_bytes=None,
        label="Checkpoints",
        help="Model weights (*.ckpt) — large",
    ),
    DownloadProfile.FULL_FORENSIC: ProfileSpec(
        frozenset(ArtifactKind), max_file_bytes=None, label="Everything", help="All files of the run (and its stages)"
    ),
}


def select(
    artifacts: Sequence[Artifact], profile: DownloadProfile, custom: ProfileSpec | None = None
) -> list[Artifact]:
    import fnmatch

    spec = (
        custom
        if profile == DownloadProfile.CUSTOM and custom
        else PROFILES.get(profile, PROFILES[DownloadProfile.FAST_DEBUG])
    )
    out = []
    for a in artifacts:
        if spec.include_globs and any(fnmatch.fnmatch(a.path, g) for g in spec.include_globs):
            out.append(a)
            continue
        if spec.match is not None:
            if not spec.match(a.path):
                continue
        elif a.kind not in spec.kinds:
            continue
        if spec.max_file_bytes is not None and (a.size_bytes or 0) > spec.max_file_bytes:
            continue
        out.append(a)
    return out


@dataclass
class DownloadProgress:
    total_files: int = 0
    done_files: int = 0
    total_bytes: int = 0
    done_bytes: int = 0
    cached_files: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    @property
    def throughput_bps(self) -> float:
        dt = time.monotonic() - self.started_at
        return self.done_bytes / dt if dt > 0 else 0.0


@dataclass(frozen=True)
class LocalArtifact:
    artifact: Artifact
    local_path: Path
    sha256: str
    from_cache: bool


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
    ) -> tuple[list[LocalArtifact], DownloadProgress]:
        """Download what is not cached (bounded workers, retries, ``.part`` resume, SHA-256). ``on_file(artifact,
        local or None, error or None)`` is called once per file as it completes, is reused, or fails."""
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
                return self._download(source, a, cancel, prog, plock, on_progress)

            return retry(attempt, self.policy)

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

    def _download(
        self,
        source: RunSource,
        a: Artifact,
        cancel: threading.Event,
        prog: DownloadProgress,
        plock: threading.Lock,
        on_progress: Callable[[DownloadProgress], None] | None,
    ) -> LocalArtifact:
        final = safe_join(self._run_dir(a), a.path)
        final.parent.mkdir(parents=True, exist_ok=True)
        part = final.with_name(final.name + ".part")
        offset = part.stat().st_size if part.exists() else 0
        with part.open("ab") as fh:
            for chunk in source.open_artifact(a.run.run_id, a.path, offset=offset):
                if cancel.is_set():
                    raise CancelledError("cancelled")
                fh.write(chunk)
                with plock:
                    prog.done_bytes += len(chunk)
                if on_progress:
                    on_progress(prog)
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
