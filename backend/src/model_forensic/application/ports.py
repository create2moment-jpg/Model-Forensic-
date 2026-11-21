"""Ports (hexagonal architecture). Infrastructure adapters implement these protocols.

Contract tests in ``tests/contract`` run against every registered implementation.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.findings import Finding
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.run import Run, RunRef
from model_forensic.domain.workspace import ForensicWorkspace


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

    def get_metric_series(self, run_id: str, keys: Sequence[MetricKey] | None = None) -> list[MetricSeries]: ...

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
    favorites_only: bool = False


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

    def get(self, ref: RunRef) -> Run | None: ...

    def set_favorite(self, ref: RunRef, favorite: bool) -> None: ...


class FindingStore(Protocol):
    def save(self, findings: Iterable[Finding]) -> None: ...

    def get(self, finding_id: str) -> Finding: ...

    def for_runs(self, refs: Sequence[RunRef]) -> list[Finding]: ...


class WorkspaceStore(Protocol):
    def save(self, ws: ForensicWorkspace) -> None: ...

    def get(self, ws_id: str) -> ForensicWorkspace: ...

    def list(self) -> list[ForensicWorkspace]: ...


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
