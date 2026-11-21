"""Application facade: orchestrates retrieval (ports) → computation (analysis) → persistence.

The API, CLI and AI tools all call this one service, so behaviour is identical across surfaces.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from model_forensic.analysis.base import AnalysisContext, AnalysisResult, Requirement, RunData
from model_forensic.analysis.comparison import compare as compute_comparison
from model_forensic.analysis.config_diff import ConfigDiffer, flatten
from model_forensic.analysis.metric_registry import MetricRegistry, default_registry
from model_forensic.analysis.registry import AnalysisRegistry, builtin_registry
from model_forensic.application.ports import (
    FindingStore,
    ListRunsQuery,
    RunSource,
    SearchFilters,
    SearchIndex,
    SearchResult,
    WorkspaceStore,
)
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.comparison import Comparison
from model_forensic.domain.config import ConfigDiff, ConfigurationSnapshot
from model_forensic.domain.findings import Finding
from model_forensic.domain.metrics import MetricKey, MetricSeries, Split
from model_forensic.domain.run import Run, RunRef
from model_forensic.domain.workspace import ForensicWorkspace
from model_forensic.infrastructure.artifacts.store import (
    ArtifactStore,
    DownloadProfile,
    DownloadProgress,
    LocalArtifact,
    select,
)
from model_forensic.infrastructure.search.query_parser import looks_natural, parse_query, rewrite_natural_language
from model_forensic.observability.telemetry import telemetry
from model_forensic.shared.errors import NotFoundError, ValidationError
from model_forensic.shared.logging import get_logger

log = get_logger(__name__)


@dataclass
class _LRU:
    capacity: int = 64
    _d: OrderedDict[str, Any] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, k: str) -> Any:
        with self._lock:
            if k in self._d:
                self._d.move_to_end(k)
                return self._d[k]
            return None

    def put(self, k: str, v: Any) -> None:
        with self._lock:
            self._d[k] = v
            self._d.move_to_end(k)
            while len(self._d) > self.capacity:
                self._d.popitem(last=False)


@dataclass(frozen=True)
class RunOverview:
    run: Run
    metric_keys: list[str]
    summary: dict[str, float]
    configs: list[ConfigurationSnapshot]
    artifacts: list[Artifact]
    baseline: RunRef | None
    what_changed: Comparison | None
    findings: list[Finding]


class ForensicService:
    def __init__(
        self,
        sources: Sequence[RunSource],
        index: SearchIndex,
        findings: FindingStore,
        workspaces: WorkspaceStore,
        artifacts: ArtifactStore,
        analyses: AnalysisRegistry | None = None,
        metrics: MetricRegistry | None = None,
        differ: ConfigDiffer | None = None,
        default_backend: str | None = None,
    ) -> None:
        self.sources = {s.name: s for s in sources}
        self.index = index
        self.findings = findings
        self.workspaces = workspaces
        self.artifact_store = artifacts
        self.analyses = analyses or builtin_registry()
        self.metrics = metrics or default_registry
        self.differ = differ or ConfigDiffer()
        self.default_backend = default_backend or (sources[0].name if sources else "local")
        self._series_cache = _LRU(64)
        self._config_cache = _LRU(256)

    # ---------- retrieval ----------
    def ref(self, value: str | RunRef) -> RunRef:
        return value if isinstance(value, RunRef) else RunRef.parse(value, self.default_backend)

    def source(self, ref: RunRef) -> RunSource:
        try:
            return self.sources[ref.backend]
        except KeyError:
            raise NotFoundError(
                f"unknown backend {ref.backend!r}", details={"available": sorted(self.sources)}
            ) from None

    def sync(self, backend: str | None = None, index_configs: bool = True, since: datetime | None = None) -> int:
        """Pull run metadata from backends into the local search index (incremental via ``since``)."""
        total = 0
        for name, src in self.sources.items():
            if backend and name != backend:
                continue
            cursor: str | None = None
            with telemetry.timed(f"sync.{name}"):
                while True:
                    page = src.list_runs(ListRunsQuery(cursor=cursor, updated_since=since))
                    params: dict[RunRef, dict[str, str]] = {}
                    if index_configs:
                        for r in page.items:
                            try:
                                params[r.ref] = {
                                    k: str(v)
                                    for k, v in self.differ.merge(self.configs(r.ref)).items()
                                    if not isinstance(v, (dict, list)) or isinstance(v, list)
                                }
                            except Exception as exc:  # degrade gracefully: index without params
                                log.warning("config indexing failed", run=str(r.ref), error=type(exc).__name__)
                    total += self.index.upsert(page.items, params)
                    if not page.next_cursor:
                        break
                    cursor = page.next_cursor
        telemetry.incr("sync.runs", total)
        return total

    def search(
        self, query: str, limit: int = 50, offset: int = 0, now: datetime | None = None, backend: str | None = None
    ) -> SearchResult:
        """Search the index. ``backend`` scopes results to one workspace (overrides any ``backend:`` in the query)."""
        with telemetry.timed("search"):
            notes: list[str] = []
            if looks_natural(query):
                dsl, notes = rewrite_natural_language(query, now)
                notes.insert(0, f"interpreted as: {dsl}")
                query = dsl
            filters = parse_query(query, now)
            if backend:
                if backend not in self.sources:
                    raise NotFoundError(f"unknown workspace {backend!r}", details={"available": sorted(self.sources)})
                filters = replace(filters, backend=backend)
            res = self.index.search(filters, limit, offset)
            res.notes[:0] = notes
            return res

    def search_filters(self, filters: SearchFilters, limit: int = 50) -> SearchResult:
        return self.index.search(filters, limit)

    def get_run(self, ref: RunRef | str) -> Run:
        r = self.ref(ref)
        cached = self.index.get(r)
        if cached is not None and cached.status.terminal:
            return cached
        with telemetry.timed("backend.get_run"):
            run = self.source(r).get_run(r.run_id)
        self.index.upsert([run])
        return run

    def series(self, ref: RunRef | str, keys: Sequence[MetricKey] | None = None) -> list[MetricSeries]:
        r = self.ref(ref)
        cache_key = str(r)
        all_series: list[MetricSeries] | None = self._series_cache.get(cache_key)
        if all_series is None:
            with telemetry.timed("backend.get_metric_series"):
                all_series = self.source(r).get_metric_series(r.run_id)
            if self.get_run(r).status.terminal:
                self._series_cache.put(cache_key, all_series)
        if keys:
            want = set(keys)
            return [s for s in all_series if s.key in want]
        return all_series

    def metric_keys(self, refs: Sequence[RunRef | str]) -> dict[str, int]:
        """Union of metric keys across runs, with the number of runs that logged each key."""
        counts: dict[str, int] = {}
        for r in refs:
            for s in self.series(r):
                counts[str(s.key)] = counts.get(str(s.key), 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def run_data(self, ref: RunRef | str) -> RunData:
        r = self.ref(ref)
        return RunData(run=self.get_run(r), series={s.key: s for s in self.series(r)})

    def configs(self, ref: RunRef | str) -> list[ConfigurationSnapshot]:
        r = self.ref(ref)
        hit: list[ConfigurationSnapshot] | None = self._config_cache.get(str(r))
        if hit is None:
            hit = self.source(r).get_configs(r.run_id)
            self._config_cache.put(str(r), hit)
        return hit

    def flat_config(self, ref: RunRef | str) -> dict[str, Any]:
        return self.differ.merge(self.configs(ref))

    def artifacts(self, ref: RunRef | str) -> list[Artifact]:
        r = self.ref(ref)
        return self.source(r).list_artifacts(r.run_id)

    def read_log(self, ref: RunRef | str, name: str | None = None, tail_bytes: int | None = 64_000) -> str:
        r = self.ref(ref)
        return self.source(r).read_log(r.run_id, name, tail_bytes)

    def search_log(self, ref: RunRef | str, pattern: str, max_hits: int = 50, context: int = 1) -> list[dict[str, Any]]:
        import re

        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValidationError(f"invalid regex: {exc}") from exc
        lines = self.read_log(ref, tail_bytes=5_000_000).splitlines()
        hits = []
        for i, line in enumerate(lines):
            if rx.search(line):
                hits.append({"line": i + 1, "text": "\n".join(lines[max(0, i - context) : i + context + 1])})
                if len(hits) >= max_hits:
                    break
        return hits

    def replicates(self, ref: RunRef | str) -> list[RunRef]:
        """Other runs in the same replicate group (``tags.group``), e.g. seeds of a baseline."""
        run = self.get_run(ref)
        group = run.tags.get("group")
        if not group:
            return []
        res = self.index.search(SearchFilters(tags=(("group", group),)), limit=50)
        return [r.ref for r in res.runs if r.ref != run.ref and r.status.terminal]

    # ---------- computation ----------
    def diff_configs(self, baseline: RunRef | str, candidate: RunRef | str) -> ConfigDiff:
        b, c = self.ref(baseline), self.ref(candidate)
        return self.differ.diff(b, self.configs(b), c, self.configs(c))

    def compare(
        self,
        baseline: RunRef | str,
        candidate: RunRef | str,
        splits: Sequence[str] = ("val", "test"),
        use_replicates: bool = True,
    ) -> Comparison:
        b, c = self.ref(baseline), self.ref(candidate)
        with telemetry.timed("compare"):
            reps = [self.run_data(r) for r in self.replicates(b)] if use_replicates else []
            return compute_comparison(
                self.run_data(b),
                self.run_data(c),
                self.diff_configs(b, c),
                self.metrics,
                reps,
                {Split(s) for s in splits},
            )

    def analyze(
        self,
        target: RunRef | str,
        baseline: RunRef | str | None = None,
        analyses: Sequence[str] | None = None,
        params: dict[str, dict[str, Any]] | None = None,
        persist: bool = True,
    ) -> list[AnalysisResult]:
        t = self.ref(target)
        b = self.ref(baseline) if baseline else None
        ctx = AnalysisContext(
            target=self.run_data(t),
            baseline=self.run_data(b) if b else None,
            baseline_replicates=[self.run_data(r) for r in self.replicates(b)] if b else [],
            config_diff=self.diff_configs(b, t) if b else None,
            registry=self.metrics,
        )
        plugins = [self.analyses.get(n) for n in analyses] if analyses else self.analyses.all()
        results: list[AnalysisResult] = []
        for plugin in plugins:
            if Requirement.BASELINE in plugin.requires and b is None:
                results.append(AnalysisResult(analysis=plugin.name, skipped_reason="requires a baseline run"))
                continue
            p = plugin.Params.model_validate((params or {}).get(plugin.name, {}))
            with telemetry.timed(f"analysis.{plugin.name}"):
                try:
                    res = plugin.run(ctx, p)
                except Exception as exc:
                    log.exception("analysis failed", analysis=plugin.name)
                    res = AnalysisResult(
                        analysis=plugin.name, skipped_reason=f"analysis error: {type(exc).__name__}: {exc}"
                    )
            results.append(res)
        if persist:
            self.findings.save([f for r in results for f in r.findings])
        return results

    def overview(self, ref: RunRef | str, baseline: RunRef | str | None = None) -> RunOverview:
        r = self.ref(ref)
        run = self.get_run(r)
        series = self.series(r)
        b = self.ref(baseline) if baseline else self.suggest_baseline(r)
        what = None
        if b is not None and b != r:
            try:
                what = self.compare(b, r)
            except NotFoundError:
                what = None
        return RunOverview(
            run=run,
            metric_keys=[str(s.key) for s in series],
            summary={str(s.key): s.aggregate() for s in series if s.key.split in (Split.VAL, Split.TEST)},
            configs=self.configs(r),
            artifacts=self.artifacts(r),
            baseline=b,
            what_changed=what,
            findings=self.findings.for_runs([r]),
        )

    def suggest_baseline(self, ref: RunRef) -> RunRef | None:
        """Most recent completed run in the same experiment tagged role=baseline, if any."""
        run = self.get_run(ref)
        if not run.experiment:
            return None
        res = self.index.search(
            SearchFilters(experiment=run.experiment, statuses=("completed",), tags=(("role", "baseline"),)), limit=5
        )
        cands = [x.ref for x in res.runs if x.ref != ref]
        return cands[0] if cands else None

    # ---------- artifacts ----------
    def download(
        self,
        ref: RunRef | str,
        profile: DownloadProfile = DownloadProfile.FAST_DEBUG,
        cancel: threading.Event | None = None,
        on_progress: Callable[[DownloadProgress], None] | None = None,
        paths: Sequence[str] | None = None,
    ) -> tuple[list[LocalArtifact], DownloadProgress]:
        r = self.ref(ref)
        arts = self.artifacts(r)
        chosen = [a for a in arts if a.path in set(paths)] if paths else select(arts, profile)
        with telemetry.timed("artifacts.download"):
            files, prog = self.artifact_store.fetch(self.source(r), chosen, cancel=cancel, on_progress=on_progress)
        telemetry.incr("artifacts.bytes", prog.done_bytes)
        telemetry.incr("artifacts.cache_hits", prog.cached_files)
        return files, prog

    # ---------- workspaces ----------
    def create_workspace(
        self, ws_id: str, title: str, runs: Sequence[str], baseline: str | None = None
    ) -> ForensicWorkspace:
        ws = ForensicWorkspace(
            id=ws_id,
            title=title,
            runs=tuple(self.ref(x) for x in runs),
            baseline=self.ref(baseline) if baseline else None,
        )
        self.workspaces.save(ws)
        return ws

    def attach_findings(self, ws_id: str, findings: Sequence[Finding]) -> ForensicWorkspace:
        ws = self.workspaces.get(ws_id)
        ids = tuple(dict.fromkeys([*ws.finding_ids, *(f.id for f in findings)]))
        ws = ws.model_copy(update={"finding_ids": ids, "updated_at": datetime.now(timezone.utc)})
        self.workspaces.save(ws)
        return ws


def flat_params(snapshots: Sequence[ConfigurationSnapshot]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in snapshots:
        out.update(flatten(s.data))
    return out
