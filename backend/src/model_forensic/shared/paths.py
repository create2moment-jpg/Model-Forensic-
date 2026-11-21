"""Filesystem safety helpers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from model_forensic.shared.errors import ValidationError


def safe_join(root: Path, relative: str) -> Path:
    """Join an untrusted relative path under ``root``, rejecting traversal and absolute paths.

    Artifact names come from remote systems and are untrusted. ``..`` components, absolute
    paths, drive letters and NUL bytes are rejected; the resolved result must stay under root.
    """
    if not relative or "\x00" in relative:
        raise ValidationError("empty or invalid artifact path")
    rel = PurePosixPath(relative.replace("\\", "/"))
    if rel.is_absolute() or any(p == ".." for p in rel.parts) or ":" in rel.parts[0]:
        raise ValidationError(f"unsafe artifact path: {relative!r}")
    root_resolved = root.resolve()
    candidate = (root_resolved / Path(*rel.parts)).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValidationError(f"artifact path escapes cache root: {relative!r}")
    return candidate
