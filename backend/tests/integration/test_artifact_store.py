import hashlib
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from model_forensic.domain.artifacts import Artifact, ArtifactKind
from model_forensic.domain.run import RunRef
from model_forensic.infrastructure.artifacts.store import ArtifactStore, DownloadProfile, select
from model_forensic.shared.errors import CancelledError
from model_forensic.shared.retry import RetryPolicy

REF = RunRef(backend="fake", run_id="r1")


class FlakySource:
    """Serves in-memory files; can fail mid-stream once to exercise resume."""

    name = "fake"

    def __init__(self, files: dict[str, bytes], fail_after: int | None = None) -> None:
        self.files = files
        self.fail_after = fail_after
        self.offsets: list[int] = []

    def open_artifact(self, run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        self.offsets.append(offset)
        data = self.files[path][offset:]
        for i in range(0, len(data), 1000):
            if self.fail_after is not None and offset + i >= self.fail_after:
                self.fail_after = None
                raise ConnectionError("reset")
            yield data[i : i + 1000]


def art(path: str, data: bytes, kind=ArtifactKind.OTHER, sha=None) -> Artifact:
    return Artifact(run=REF, path=path, kind=kind, size_bytes=len(data), sha256=sha)


def store(tmp_path: Path, **kw) -> ArtifactStore:
    return ArtifactStore(tmp_path / "a", min_free_bytes=0, policy=RetryPolicy(max_attempts=3, base_delay_s=0), **kw)


def test_resume_after_interruption(tmp_path: Path) -> None:
    data = bytes(range(256)) * 40
    src = FlakySource({"ckpt.pt": data}, fail_after=5000)
    files, prog = store(tmp_path).fetch(src, [art("ckpt.pt", data)])
    assert files[0].local_path.read_bytes() == data
    assert src.offsets == [0, 5000]  # second attempt resumed, not restarted
    assert not prog.failed


def test_cache_hit_and_dedup(tmp_path: Path) -> None:
    data = b"x" * 3000
    s = store(tmp_path)
    src = FlakySource({"a.pt": data, "b.pt": data})
    s.fetch(src, [art("a.pt", data), art("b.pt", data)])
    _, prog = s.fetch(src, [art("a.pt", data)])
    assert prog.cached_files == 1
    assert len([p for p in s.blobs.rglob("*") if p.is_file()]) == 1  # identical content stored once


def test_checksum_mismatch_is_reported(tmp_path: Path) -> None:
    data = b"hello" * 100
    _, prog = store(tmp_path).fetch(FlakySource({"x": data}), [art("x", data, sha="0" * 64)])
    assert prog.failed and "IntegrityError" in prog.failed[0][1]


def test_checksum_match(tmp_path: Path) -> None:
    data = b"hello" * 100
    files, _ = store(tmp_path).fetch(FlakySource({"x": data}), [art("x", data, sha=hashlib.sha256(data).hexdigest())])
    assert files[0].sha256 == hashlib.sha256(data).hexdigest()


def test_cancellation(tmp_path: Path) -> None:
    ev = threading.Event()
    ev.set()
    with pytest.raises(CancelledError):
        store(tmp_path).fetch(FlakySource({"x": b"1" * 5000}), [art("x", b"1" * 5000)], cancel=ev)


def test_traversal_in_artifact_name_rejected(tmp_path: Path) -> None:
    _, prog = store(tmp_path).fetch(FlakySource({"../../evil": b"x"}), [art("../../evil", b"x")])
    assert prog.failed and "unsafe" in prog.failed[0][1]
    assert not (tmp_path / "evil").exists()


def test_retention_evicts_lru(tmp_path: Path) -> None:
    s = store(tmp_path, max_bytes=2500)
    a, b = b"a" * 2000, b"b" * 2000
    s.fetch(FlakySource({"a": a}), [art("a", a)])
    s.fetch(FlakySource({"b": b}), [art("b", b)])
    assert s.usage_bytes() <= 2500
    assert s.cached(art("b", b)) is not None and s.cached(art("a", a)) is None


def test_profiles_skip_large_checkpoints() -> None:
    arts = [
        art("logs/std_log.txt", b"x", ArtifactKind.LOG),
        Artifact(run=REF, path="ckpt.pt", kind=ArtifactKind.CHECKPOINT, size_bytes=8 * 1024**3),
        Artifact(run=REF, path="eval.json", kind=ArtifactKind.EVALUATION, size_bytes=100),
    ]
    assert [a.path for a in select(arts, DownloadProfile.FAST_DEBUG)] == ["logs/std_log.txt", "eval.json"]
    assert [a.path for a in select(arts, DownloadProfile.CHECKPOINT_ONLY)] == ["ckpt.pt"]
