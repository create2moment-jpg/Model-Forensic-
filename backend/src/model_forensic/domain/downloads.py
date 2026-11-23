"""What to download from a run: the download profiles, selection of a run's files by profile, progress and the
local copy of a file. Pure (no I/O); the artifact cache (``infrastructure/artifacts/store.py``) implements the fetching.

Moved out of the artifact store (design review D10) so the application uses it without importing an adapter."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from model_forensic.domain.artifacts import Artifact, ArtifactKind


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
