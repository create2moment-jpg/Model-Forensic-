"""Ports (hexagonal architecture). Infrastructure adapters implement these protocols.

Contract tests in ``tests/contract`` run against every registered implementation.
"""

from __future__ import annotations

import builtins
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.downloads import DownloadProgress, LocalArtifact
from model_forensic.domain.findings import Finding
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.run import Run, RunRef
from model_forensic.domain.study import Study


@dataclass(frozen=True)
class Page:
    """Cursor-based pagination for backend listing."""

    items: Sequence[Run]
    next_cursor: str | None = None


@dataclass(frozen=True)
class ListRunsQuery:
    experiment: str | None = None
    updated_since: datetime | None = None
    cursor: str | None = None
    page_size: int = 200


@runtime_checkable
class RunSource(Protocol):
    """A backend connector (Azure ML, local filesystem, MLflow, W&B, ...).

    Read-only by contract. Implementations must map backend objects to domain types and never
    leak SDK objects through this boundary.
    """

    name: str

    def list_runs(self, query: ListRunsQuery) -> Page: ...

    def get_run(self, run_id: str) -> Run: ...

    def list_metric_keys(self, run_id: str) -> list[MetricKey]: ...

    def get_metric_series(
        self,
        run_id: str,
        keys: Sequence[MetricKey] | None = None,
        on_progress: Callable[[int, int], None] | None = None,  # (done, total) per metric history; optional
    ) -> list[MetricSeries]: ...

    def get_configs(self, run_id: str) -> list[ConfigurationSnapshot]: ...

    def list_artifacts(self, run_id: str) -> list[Artifact]: ...

    def open_artifact(self, run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        """Stream artifact bytes starting at ``offset`` (for resumable downloads)."""
        ...

    def read_log(self, run_id: str, name: str | None = None, tail_bytes: int | None = None) -> str: ...


@dataclass(frozen=True)
class SearchFilters:
    text: str | None = None
    statuses: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    experiment: str | None = None
    user: str | None = None
    users: tuple[str, ...] = ()  # any of these (case-insensitive); `user:me` expands to your names
    experiment_owners: tuple[str, ...] = ()
    backends: tuple[str, ...] = ()  # any of these workspaces (a project's)
    experiment_globs: tuple[
        str, ...
    ] = ()  # experiment name globs (a project's); empty = any  # experiments where any of these users has a run (`experiment:mine`)
    compute: str | None = None
    branch: str | None = None
    commit: str | None = None
    backend: str | None = None
    tags: tuple[tuple[str, str | None], ...] = ()
    params: tuple[tuple[str, str], ...] = ()  # config/params equality on flattened path
    metric_ranges: tuple[tuple[str, float | None, float | None], ...] = ()
    created_after: datetime | None = None
    created_before: datetime | None = None
    parent: str | None = None
    stages: str = "all"  # "all" · "hide" (pipelines and plain runs: `is:top`) · "only" (pipeline stages: `is:stage`)
    favorites_only: bool = False
    starred_experiments_only: bool = False  # runs of starred experiments (`exp:starred`)


@dataclass(frozen=True)
class SearchResult:
    runs: list[Run]
    total: int
    took_ms: float
    applied: SearchFilters
    notes: list[str] = field(default_factory=list)


class SearchIndex(Protocol):
    def upsert(self, runs: Iterable[Run], params: dict[RunRef, dict[str, str]] | None = None) -> int: ...

    def search(self, filters: SearchFilters, limit: int = 50, offset: int = 0) -> SearchResult: ...

    def suggest(self, prefix: str, field: str = "name", limit: int = 10) -> list[str]: ...

    def experiments(self, mine: set[str], backend: str | None = None) -> list[dict[str, Any]]: ...

    def update_summary(self, ref: RunRef, values: dict[str, float]) -> None: ...

    def get(self, ref: RunRef) -> Run | None: ...

    def set_favorite(self, ref: RunRef, favorite: bool) -> None: ...

    def star_experiment(self, name: str, starred: bool) -> None: ...

    def counts_by_backend(self) -> dict[str, int]: ...

    def mark_synced(self, backend: str, at: datetime) -> None: ...

    def last_synced(self) -> dict[str, datetime]: ...

    def starred_experiments(self) -> set[str]: ...


class FindingStore(Protocol):
    def save(self, findings: Iterable[Finding]) -> None: ...

    def get(self, finding_id: str) -> Finding: ...

    def for_runs(self, refs: Sequence[RunRef]) -> list[Finding]: ...


class StudyStore(Protocol):
    def save(self, study: Study) -> None: ...

    def get(self, study_id: str) -> Study: ...

    def list(self) -> list[Study]: ...

    def delete(self, study_id: str) -> None: ...

    def summaries(
        self, status: str | None = None, text: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[builtins.list[dict[str, Any]], int]: ...


@dataclass(frozen=True)
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: list[dict[str, Any]]
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float | None = None


class LLMProvider(Protocol):
    """Provider-agnostic chat completion. BYOK; OpenAI-compatible is the reference adapter."""

    model: str

    def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
        *,
        json_mode: bool = False,
    ) -> LLMResponse: ...


def is_offline(llm: object) -> bool:
    """True for the deterministic stand-in used when no LLM is connected (it sets ``offline = True``)."""
    return bool(getattr(llm, "offline", False))


class ArtifactCache(Protocol):
    """Local cache of a run's files: what is there, fetching the rest (``infrastructure/artifacts/store.py``)."""

    workers: int

    def run_root(self, ref: RunRef) -> Path: ...

    def cached(self, a: Artifact, deep: bool = False) -> LocalArtifact | None: ...

    def partial_bytes(self, a: Artifact) -> int: ...

    def drop_partial(self, a: Artifact) -> None: ...

    def free_bytes(self) -> int: ...

    def usage_bytes(self) -> int: ...

    def forget(
        self, backend: str | None = None, run_id: str | None = None, keep: Callable[[str], bool] | None = None
    ) -> tuple[int, int]: ...

    def fetch(
        self,
        source: RunSource,
        artifacts: Sequence[Artifact],
        *,
        cancel: threading.Event | None = None,
        on_progress: Callable[[DownloadProgress], None] | None = None,
        on_file: Callable[[Artifact, LocalArtifact | None, str | None], None] | None = None,
        on_stage: Callable[[Artifact, str, dict[str, Any]], None] | None = None,
    ) -> tuple[list[LocalArtifact], DownloadProgress]: ...


class Notifier(Protocol):
    """Sends a short message to the team (``infrastructure/notify.py``: a Teams / JSON webhook)."""

    sent: list[dict[str, Any]]

    @property
    def url(self) -> str | None: ...

    def send(self, title: str, lines: list[str], link: str | None = None) -> bool: ...
