"""On-disk cache of a finished run's metadata (metric series, configs, file listing).

Only runs in a terminal state are cached (their metadata no longer changes); running runs always come from the
backend. Files are gzip JSON under ``<data_dir>/meta/``, one per (run, kind), written atomically; the cache keeps
itself under a byte budget by dropping the least recently used files. Reading a corrupt or outdated file is a miss,
never an error — the backend is the source of truth.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)
VERSION = 1
KINDS = ("series", "configs", "artifacts")


class MetaCache:
    def __init__(self, folder: Path, budget_bytes: int = 2 * 1024**3) -> None:
        self.folder = folder
        self.budget = budget_bytes
        self._lock = threading.Lock()

    def _path(self, run: str, kind: str) -> Path:
        h = hashlib.sha256(run.encode()).hexdigest()[:32]
        return self.folder / h[:2] / f"{h}.{kind}.json.gz"

    def get(self, run: str, kind: str, model: type[M]) -> list[M] | None:
        p = self._path(run, kind)
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("v") != VERSION or doc.get("run") != run:
                return None
            items = [model.model_validate(x) for x in doc["items"]]
            os.utime(p, None)  # recently used
            return items
        except (OSError, ValueError, KeyError, ValidationError, EOFError):
            return None

    def put(self, run: str, kind: str, items: Sequence[BaseModel]) -> None:
        p = self._path(run, kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        body = {"v": VERSION, "run": run, "at": time.time(), "items": [x.model_dump(mode="json") for x in items]}
        try:
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as f:
                json.dump(body, f, separators=(",", ":"))
            tmp.replace(p)
        except OSError:
            tmp.unlink(missing_ok=True)
            return
        self._prune()

    def has(self, run: str, kinds: Sequence[str] = KINDS) -> bool:
        return all(self._path(run, k).is_file() for k in kinds)

    def drop(self, run: str) -> None:
        for k in KINDS:
            self._path(run, k).unlink(missing_ok=True)

    def size(self) -> int:
        return sum(p.stat().st_size for p in self.folder.glob("*/*.json.gz")) if self.folder.is_dir() else 0

    def _prune(self) -> None:
        with self._lock:
            files: list[tuple[float, int, Path]] = []
            total = 0
            for p in self.folder.glob("*/*.json.gz"):
                try:
                    st = p.stat()
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, p))
                total += st.st_size
            if total <= self.budget:
                return
            for _mtime, size, p in sorted(files):
                p.unlink(missing_ok=True)
                total -= size
                if total <= self.budget * 0.9:
                    break


def stats(cache: MetaCache | None) -> dict[str, Any]:
    return {"enabled": cache is not None, "bytes": cache.size() if cache else 0}
