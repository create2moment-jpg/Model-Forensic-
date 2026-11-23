"""Application facade: orchestrates retrieval (ports) → computation (analysis) → persistence.

The API, CLI and AI tools all call this one service, so behaviour is identical across surfaces.
"""

from __future__ import annotations

import contextlib
import inspect
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from model_forensic.analysis.base import AnalysisContext, AnalysisResult, Requirement, RunData
from model_forensic.analysis.comparison import compare as compute_comparison
from model_forensic.analysis.config_diff import ConfigDiffer, flatten
from model_forensic.analysis.evaljson import EvalEvidence
from model_forensic.analysis.metric_registry import MetricRegistry, default_registry
from model_forensic.analysis.registry import AnalysisRegistry, builtin_registry
from model_forensic.analysis.sequences import SequenceTable
from model_forensic.application.ports import (
    ArtifactCache,
    FindingStore,
    ListRunsQuery,
    RunSource,
    SearchFilters,
    SearchIndex,
    SearchResult,
)
from model_forensic.application.query_parser import looks_natural, parse_query, rewrite_natural_language
from model_forensic.application.stages import merge_stage_series
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.comparison import Comparison
from model_forensic.domain.config import ConfigDiff, ConfigurationSnapshot
from model_forensic.domain.downloads import PROFILES, DownloadProfile, DownloadProgress, LocalArtifact, select
from model_forensic.domain.findings import Finding
from model_forensic.domain.metrics import MetricKey, MetricSeries, Split
from model_forensic.domain.run import Run, RunRef
from model_forensic.interface.team import TeamPolicy
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
                v, expires = self._d[k]
                if expires is not None and time.monotonic() > expires:
                    del self._d[k]
                    return None
                self._d.move_to_end(k)
                return v
            return None

    def put(self, k: str, v: Any, ttl_s: float | None = None) -> None:
        """``ttl_s`` None = until evicted (finished runs never change); a number for running runs."""
        with self._lock:
            self._d[k] = (v, None if ttl_s is None else time.monotonic() + ttl_s)
            self._d.move_to_end(k)
            while len(self._d) > self.capacity:
                self._d.popitem(last=False)

    def drop(self, k: str) -> None:
        with self._lock:
            self._d.pop(k, None)

    def __contains__(self, k: str) -> bool:
        return self.get(k) is not None


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
        artifacts: ArtifactCache,
        analyses: AnalysisRegistry | None = None,
        metrics: MetricRegistry | None = None,
        differ: ConfigDiffer | None = None,
        default_backend: str | None = None,
    ) -> None:
        self.sources = {s.name: s for s in sources}
        self.sync_errors: dict[str, str] = {}
        self.sync_ok: set[str] = set()  # workspaces a sync has actually reached
        self._artifact_cache = _LRU(256)
        self._tables_cache = _LRU(16)
        self._evals_cache = _LRU(32)
        self.team = TeamPolicy()
        self.eval_tables_budget_bytes = 200 * 1024**2
        # Implicit downloads (logs, configs, Lightning metrics.csv) stay under this per request; evaluation JSONs,
        # parquets, plots and checkpoints are only fetched when someone asks (Artifacts / Prefetch / a Download button).
        self.auto_fetch_max_bytes = 50 * 1024**2
        self.local_plugins: set[str] = set()
        self.index = index
        self.findings = findings
        self.artifact_store = artifacts
        self.analyses = analyses or builtin_registry()
        self.metrics = metrics or default_registry
        self.differ = differ or ConfigDiffer()
        self.default_backend = default_backend or (sources[0].name if sources else "local")
        self._series_cache = _LRU(64)
        self._series_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.me: Callable[[], list[str]] = list  # names identifying the current user in runs (set by the container)
        self._running_cache = _LRU(128)
        self.running_ttl_s = 30.0  # running runs: metrics re-fetched at most every 30 s
        self.local_ttl_s = 5.0  # local folders: run status re-read after a few seconds
        self._local_versions: dict[str, str] = {}  # local runs: file fingerprint when their data was cached
        self._config_cache = _LRU(256)
        self.projects: list[Any] = []  # projects.ProjectProfile — set by the container
        self.meta_cache: Any = None  # infrastructure.persistence.meta_cache.MetaCache — finished runs on disk

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
            try:
                total += self._sync_one(name, src, index_configs, since)
                self.sync_errors.pop(name, None)
                self.sync_ok.add(name)
            except Exception as exc:
                # One unreachable workspace (credential, network, permissions) must not stop the others.
                self.sync_errors[name] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}"
                log.warning("sync failed", backend=name, error=self.sync_errors[name])
                if backend:
                    raise
        telemetry.incr("sync.runs", total)
        return total

    def _sync_one(self, name: str, src: RunSource, index_configs: bool, since: datetime | None) -> int:
        total = 0
        cursor: str | None = None
        with telemetry.timed(f"sync.{name}"):
            while True:
                page = src.list_runs(ListRunsQuery(cursor=cursor, updated_since=since))
                params: dict[RunRef, dict[str, str]] = {}
                if index_configs and getattr(src, "index_configs_on_sync", True):
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
        return total

    def search(
        self,
        query: str,
        limit: int = 50,
        offset: int = 0,
        now: datetime | None = None,
        backend: str | None = None,
        project: str | None = None,
    ) -> SearchResult:
        """Search the index. ``backend`` scopes results to one workspace (overrides any ``backend:`` in the query)."""
        with telemetry.timed("search"):
            notes: list[str] = []
            if looks_natural(query):
                dsl, notes = rewrite_natural_language(query, now)
                notes.insert(0, f"interpreted as: {dsl}")
                query = dsl
            filters = parse_query(query, now)
            if filters.user and filters.user.lower() == "me":
                filters = replace(filters, user=None, users=self._my_names())
            if filters.experiment and filters.experiment.lower() in ("mine", "me"):
                filters = replace(filters, experiment=None, experiment_owners=self._my_names())
            if project:
                p = next((x for x in self.projects if x.id == project), None)
                if p is None:
                    raise NotFoundError(
                        f"unknown project {project!r}", details={"available": [x.id for x in self.projects]}
                    )
                filters = replace(filters, backends=tuple(p.backends), experiment_globs=tuple(p.experiments))
            if backend:
                if backend not in self.sources:
                    raise NotFoundError(f"unknown workspace {backend!r}", details={"available": sorted(self.sources)})
                filters = replace(filters, backend=backend)
            res = self.index.search(filters, limit, offset)
            res.notes[:0] = notes
            return res

    def _my_names(self) -> tuple[str, ...]:
        names = self.me()
        if not names:
            raise ValueError(
                'Who is \'me\'? Add `me: ["Your Name", "you@company.com"]` under `defaults:` in '
                "config/azure.local.yaml (or Settings → Azure), or sign in with `az login`."
            )
        return tuple(names)

    def experiments(self, backend: str | None = None, project: str | None = None) -> list[dict[str, Any]]:
        """Experiments with their run counts, newest activity, and whether you have runs in them (yours first)."""
        try:
            mine = {n.lower() for n in self.me()}
        except Exception:  # who "me" is may be unknown — the list still works
            mine = set()
        out = self.index.experiments(mine, backend)
        p = next((x for x in self.projects if x.id == project), None) if project else None
        if p is not None:
            out = [e for e in out if p.owns_experiment(e["name"])]
        return out

    def project_of(self, ref: RunRef | str) -> Any:
        """The project profile a run belongs to (by its workspace and experiment), or None."""
        r = self.ref(ref)
        for p in self.projects:
            if r.backend in p.backends:
                try:
                    exp = self.get_run(r).experiment
                except Exception:
                    exp = None
                if p.owns_experiment(exp):
                    return p
        return None

    def search_filters(self, filters: SearchFilters, limit: int = 50) -> SearchResult:
        return self.index.search(filters, limit)

    def get_run(self, ref: RunRef | str) -> Run:
        r = self.ref(ref)
        cached = self.index.get(r)
        # A finished run from the index is final — except a pipeline whose stages were never resolved: job
        # listings carry no children, so the stages come from one get_run on the backend (then cached).
        unresolved = (
            cached is not None
            and cached.kind.value == "pipeline"
            and not cached.children
            and "stages" not in cached.backend_extras
        )
        if cached is not None and cached.status.terminal and not unresolved and not self._mutable(r):
            return cached  # a finished run no longer changes; a local folder can (training resumed)
        if unresolved and cached is not None:
            try:
                with telemetry.timed("backend.get_run"):
                    run = self.source(r).get_run(r.run_id)
            except Exception as exc:  # keep the indexed copy usable when the backend call fails
                log.warning("stage resolution failed", run=str(r), error=type(exc).__name__)
                return cached
            if "stages" in run.backend_extras:
                self.index.upsert([run])
            return run
        fresh: Run | None = self._running_cache.get(str(r))
        if fresh is not None:
            return fresh
        with telemetry.timed("backend.get_run"):
            run = self.source(r).get_run(r.run_id)
        if getattr(self.source(r), "index_runs", True):  # run groups are built, never indexed
            self.index.upsert([run])
        if not run.status.terminal or self._mutable(r):
            self._running_cache.put(
                str(r), run, self.local_ttl_s if self._mutable(r) else min(10.0, self.running_ttl_s)
            )
        return run

    def _mutable(self, r: RunRef) -> bool:
        """A local folder that can still change (its source fingerprints the files: ``version``)."""
        return not self._remote(r) and hasattr(self.sources.get(r.backend), "version")

    def _remote(self, r: RunRef) -> bool:
        return bool(getattr(self.sources.get(r.backend), "remote", True))

    def _ttl(self, r: RunRef, terminal: bool) -> float | None:
        """How long memory may keep a run's data: a finished remote run for good, a running one 30 s. A local run
        is kept until its files change (``_local_changed``)."""
        if not self._remote(r):
            return None
        return None if terminal else self.running_ttl_s

    def _local_changed(self, r: RunRef) -> bool:
        """A local folder run whose files changed since its data was cached: drop the cache (it is re-read at once).
        Sources without a ``version`` (fixed exports, the demo) never change."""
        src = self.sources.get(r.backend)
        version = getattr(src, "version", None)
        if self._remote(r) or version is None:
            return False
        try:
            now = str(version(r.run_id))
        except Exception:
            return False
        before = self._local_versions.get(str(r))
        self._local_versions[str(r)] = now
        if before is not None and before != now:
            for c in (self._series_cache, self._config_cache, self._artifact_cache, self._running_cache):
                c.drop(str(r))
            return True
        return False

    def _fetch_series(self, r: RunRef, report: Callable[[int, int], None] | None) -> list[MetricSeries]:
        src = self.source(r)
        try:
            accepts = "on_progress" in inspect.signature(src.get_metric_series).parameters
        except (TypeError, ValueError):
            accepts = False
        if accepts and report is not None:
            return src.get_metric_series(r.run_id, on_progress=report)
        out = src.get_metric_series(r.run_id)
        if report is not None:
            report(len(out), len(out))
        return out

    def series(
        self,
        ref: RunRef | str,
        keys: Sequence[MetricKey] | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[MetricSeries]:
        """All metric series of a run (pipeline stages merged), cached. One fetch per run at a time: a second caller
        waits for the first instead of repeating hundreds of requests. ``progress({stage, done, total})`` reports
        each history as it arrives (per stage)."""
        r = self.ref(ref)
        cache_key = str(r)
        self._local_changed(r)  # a local run still writing: its new values are read now
        all_series: list[MetricSeries] | None = self._series_cache.get(cache_key)
        if all_series is None:
            with self._series_lock(cache_key):
                all_series = self._series_cache.get(cache_key)
                if all_series is None and self._disk_ok(r):
                    all_series = self.meta_cache.get(cache_key, "series", MetricSeries)
                    if all_series is not None:
                        self._series_cache.put(cache_key, all_series)
                if all_series is None:
                    all_series = self._load_series(r, progress)
        if keys:
            want = set(keys)
            return [s for s in all_series if s.key in want]
        return all_series

    def _series_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._series_locks.setdefault(key, threading.Lock())

    def _load_series(self, r: RunRef, progress: Callable[[dict[str, Any]], None] | None) -> list[MetricSeries]:
        def rep(stage: str | None) -> Callable[[int, int], None] | None:
            if progress is None:
                return None
            return lambda done, total: progress({"stage": stage, "done": done, "total": total})

        with telemetry.timed("backend.get_metric_series"):
            all_series = self._fetch_series(r, rep(None))
            stages = self.stages(r)
            if stages:

                def one(name: str, c: RunRef) -> list[MetricSeries] | None:
                    try:
                        # a run group's members may be pipelines: each member fully merged; a stage: its own
                        # series, shared with a later look at that stage alone (``stage_series``)
                        return (
                            self.series(c, progress=progress)
                            if r.backend == "group"
                            else self._child_series(c, rep(name))
                        )
                    except Exception as exc:  # partial result: the other stages still show
                        log.warning("stage metrics unavailable", stage=name, error=type(exc).__name__)
                        return None

                # the stages in parallel (each one's requests already go through the backend's rate limit)
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=max(1, min(4, len(stages))), thread_name_prefix="mf-stage") as ex:
                    got = list(ex.map(lambda st: one(*st), stages))
                per_stage = [(name, g) for (name, _), g in zip(stages, got, strict=True) if g is not None]
                all_series = merge_stage_series(all_series, per_stage)
        terminal = self.get_run(r).status.terminal
        self._series_cache.put(str(r), all_series, self._ttl(r, terminal))
        if terminal and self._disk_ok(r):
            self.meta_cache.put(str(r), "series", all_series)
        return all_series

    def _child_series(self, c: RunRef, report: Callable[[int, int], None] | None) -> list[MetricSeries]:
        """One stage run's own series, cached under the stage run (fetched once for the pipeline and the stage)."""
        key = str(c)
        hit: list[MetricSeries] | None = self._series_cache.get(key)
        if hit is not None:
            return hit
        with self._series_lock(key):
            hit = self._series_cache.get(key)
            if hit is None and self._disk_ok(c):
                hit = self.meta_cache.get(key, "series", MetricSeries)
            if hit is None:
                hit = self._fetch_series(c, report)
                terminal = self.get_run(c).status.terminal
                if terminal and self._disk_ok(c):
                    self.meta_cache.put(key, "series", hit)
                self._series_cache.put(key, hit, self._ttl(c, terminal))
            else:
                self._series_cache.put(key, hit)
            return hit

    def stage_series(self, ref: RunRef | str, stage: str | None = None) -> list[MetricSeries]:
        """What one stage logged, under plain keys (``stages.stage_view``) — without waiting for the other stages:
        when the pipeline's merged series are not loaded yet, only that stage run is fetched. ``stage=None``: each
        metric from its last stage (needs the whole pipeline)."""
        from model_forensic.application.stages import stage_view

        r = self.ref(ref)
        if stage is None:
            return stage_view(self.series(r), None)
        child = next((c for n, c in self.stages(r) if n == stage), None)
        merged_ready = str(r) in self._series_cache or (self._disk_ok(r) and self.meta_cache.has(str(r), ("series",)))
        if child is None or merged_ready or r.backend == "group":
            return stage_view(self.series(r), stage)
        own = self._child_series(child, None)
        return [
            s.model_copy(update={"provenance": {**s.provenance, "stage": stage}})
            for s in own
            if "stage" not in s.key.dim_dict
        ]

    def stages(self, ref: RunRef | str) -> list[tuple[str, RunRef]]:
        """``(stage name, child ref)`` in pipeline order, for pipeline runs; empty otherwise."""
        r = self.ref(ref)
        run = self.get_run(r)
        if not run.children:
            return []
        named = dict(part.split("=", 1) for part in str(run.backend_extras.get("stages", "")).split(",") if "=" in part)
        remember = getattr(self.source(r), "remember_experiment", None)
        if remember is not None and run.experiment:
            for c in run.children:
                remember(c.run_id, run.experiment)
        out = []
        for c in run.children:
            name = named.get(c.run_id)
            if not name:
                try:
                    child = self.get_run(c)
                    name = child.display_name or child.name
                except Exception:  # child not reachable → keep its id
                    name = c.run_id
            out.append((name, c))
        return out

    def metric_keys(self, refs: Sequence[RunRef | str]) -> dict[str, int]:
        """Union of metric keys across runs, with the number of runs that logged each key."""
        counts: dict[str, int] = {}
        for r in refs:
            for s in self.series(r):
                counts[str(s.key)] = counts.get(str(s.key), 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def run_data(
        self, ref: RunRef | str, with_tables: bool = False, with_evals: bool = False, with_config: bool = False
    ) -> RunData:
        r = self.ref(ref)
        return RunData(
            run=self.get_run(r),
            series={s.key: s for s in self.series(r)},
            tables=self.eval_tables(r) if with_tables else (),
            evals=self.eval_json(r) if with_evals else (),
            configs=self.flat_config(r) if with_config else {},
        )

    def lr_series(self, ref: RunRef | str, stage: str | None = None) -> MetricSeries | None:
        """The LR schedule: a logged ``train/lr`` (``lr-AdamW`` etc.), else the training stage's Lightning metrics.csv.
        ``stage``: that pipeline stage's schedule (read from the stage run alone)."""
        import csv
        import io

        r = self.ref(ref)
        child = next((c for n, c in self.stages(r) if n == stage), None) if stage else None
        if child is not None and r.backend != "group":
            return self.lr_series(child)
        for s in self.series(r):
            if s.key.name == "lr" and s.key.split == Split.TRAIN and not s.key.dims:
                return s
        plan = [a for a in self.download_plan(r, DownloadProfile.METRICS_CSV) if (a.size_bytes or 0) < 50_000_000]
        if not plan:
            return None
        files, _ = self.download(r, DownloadProfile.METRICS_CSV, paths=[f"{a.run}::{a.path}" for a in plan[:1]])
        if not files:
            return None
        steps: list[float] = []
        vals: list[float] = []
        reader = csv.DictReader(io.StringIO(files[0].local_path.read_text(errors="replace")))
        col = next((c for c in (reader.fieldnames or []) if c.startswith("lr") or c == "learning_rate"), None)
        if col is None:
            return None
        for row in reader:
            try:
                st, v = float(row.get("step") or "nan"), float(row.get(col) or "nan")
            except ValueError:
                continue
            if st == st and v == v:
                steps.append(st)
                vals.append(v)
        if not steps:
            return None
        order = sorted(range(len(steps)), key=lambda i: steps[i])
        return MetricSeries(
            key=MetricKey(name="lr", split=Split.TRAIN),
            steps=tuple(steps[i] for i in order),
            values=tuple(vals[i] for i in order),
            source="metrics.csv",
            provenance={"artifact": files[0].artifact.path, "column": col},
        )

    def pending(
        self, ref: RunRef | str, profile: DownloadProfile, stages: Sequence[str] | None = None
    ) -> dict[str, Any]:
        """What a view would need that is not downloaded yet (files, known bytes) — shown with a Download button."""
        r = self.ref(ref)
        plan = self.download_plan(r, profile, stages)
        miss = [a for a in plan if self.artifact_store.cached(a) is None]
        return {
            "profile": profile.value,
            "files": len(miss),
            "bytes": sum(a.size_bytes or 0 for a in miss),
            "unknown_sizes": sum(1 for a in miss if a.size_bytes is None),
            "total_files": len(plan),
        }

    def _cached_or_fetch(
        self, r: RunRef, profile: DownloadProfile, stages: Sequence[str] | None, fetch: bool
    ) -> tuple[list[LocalArtifact], bool]:
        """(local files, complete?). Without ``fetch`` only what is already in the cache is used — no download."""
        plan = self.download_plan(r, profile, stages)
        if not plan:
            return [], True
        if fetch or not getattr(self.sources.get(r.backend), "remote", True):  # a local folder is read in place
            uncached = sum(a.size_bytes or 0 for a in plan if self.artifact_store.cached(a) is None)
            if uncached > self.eval_tables_budget_bytes:
                log.warning("download over budget", run=str(r), profile=profile.value, bytes=uncached)
            else:
                return self.download(r, profile, stages=stages)[0], True
        hits = [self.artifact_store.cached(a) for a in plan]
        have = [h for h in hits if h is not None]
        return have, len(have) == len(plan)

    def eval_json(self, ref: RunRef | str, fetch: bool = False) -> list[EvalEvidence]:
        """Per-class evaluation JSONs, one EvalEvidence per evaluation (pipelines: per stage, in stage order).
        Reads what is downloaded; ``fetch`` downloads the rest first (only on an explicit request)."""
        from model_forensic.analysis import evaljson

        r = self.ref(ref)
        hit: list[EvalEvidence] | None = self._evals_cache.get(str(r))
        if hit is not None:
            return hit
        files, complete = self._cached_or_fetch(r, DownloadProfile.EVAL_JSON, None, fetch)
        if not files:
            return []
        groups: dict[tuple[str, str | None], list[tuple[str, Path]]] = {}
        for f in files:
            groups.setdefault((str(f.artifact.run), f.artifact.stage), []).append((f.artifact.path, f.local_path))
        order = {name: i for i, (name, _) in enumerate(self.stages(r))}
        out = [evaljson.load(fs, stage=stage) for (_, stage), fs in groups.items()]
        out = [e for e in out if not e.empty]
        out.sort(key=lambda e: order.get(e.stage or "", -1))
        if complete:  # a partial read is not remembered: after a download the full set is read
            self._evals_cache.put(str(r), out)
        return out

    def eval_tables(
        self, ref: RunRef | str, stages: Sequence[str] | None = None, fetch: bool = False
    ) -> list[SequenceTable]:
        """Per-sequence evaluation parquets of the run (pipelines: every evaluation stage). Reads what is downloaded;
        ``fetch`` downloads the rest first (only on an explicit request)."""
        from model_forensic.analysis import sequences

        r = self.ref(ref)
        key = f"{r}|{','.join(stages or [])}"
        hit: list[SequenceTable] | None = self._tables_cache.get(key)
        if hit is not None:
            return hit
        files, complete = self._cached_or_fetch(r, DownloadProfile.EVAL_PARQUET, stages, fetch)
        if not files:
            return []
        order = {name: i for i, (name, _) in enumerate(self.stages(r))}
        files = sorted(files, key=lambda f: (order.get(f.artifact.stage or "", -1), f.artifact.path))
        out = [sequences.load(f.local_path, remote_path=f.artifact.path, stage=f.artifact.stage) for f in files]
        if complete:
            self._tables_cache.put(key, out)
        return out

    def configs(self, ref: RunRef | str) -> list[ConfigurationSnapshot]:
        r = self.ref(ref)
        self._local_changed(r)
        hit: list[ConfigurationSnapshot] | None = self._config_cache.get(str(r))
        if hit is None and self._disk_ok(r):
            hit = self.meta_cache.get(str(r), "configs", ConfigurationSnapshot)
            if hit is not None:
                self._config_cache.put(str(r), hit)
        if hit is None:
            hit = self.source(r).get_configs(r.run_id)
            # Pipelines: the composed configs live in the stages (e.g. stage_output/summaries/train/composed_config.yaml).
            if not [c for c in hit if c.source != "params"]:
                for name, c in self.stages(r):
                    try:
                        snaps = self.configs(c) if r.backend == "group" else self.source(c).get_configs(c.run_id)
                        hit += [snap.model_copy(update={"source": f"{name}/{snap.source}"}) for snap in snaps]
                    except Exception as exc:
                        log.warning("stage config unavailable", stage=name, error=type(exc).__name__)
            self._config_cache.put(str(r), hit)
            if self._disk_ok(r):
                self.meta_cache.put(str(r), "configs", hit)
        return hit

    def flat_config(self, ref: RunRef | str) -> dict[str, Any]:
        return self.differ.merge(self.configs(ref))

    def artifacts(self, ref: RunRef | str) -> list[Artifact]:
        """The run's files; for pipelines also every stage's files (``Artifact.stage``, ``Artifact.run`` = stage)."""
        r = self.ref(ref)
        self._local_changed(r)
        hit: list[Artifact] | None = self._artifact_cache.get(str(r))
        if hit is not None:
            return hit
        if self._disk_ok(r):
            disk = self.meta_cache.get(str(r), "artifacts", Artifact)
            if disk is not None:
                self._artifact_cache.put(str(r), disk)
                return list(disk)
        out = list(self.source(r).list_artifacts(r.run_id))
        for name, c in self.stages(r):
            try:
                if r.backend == "group":  # members may be pipelines: their stage files too
                    out += [
                        a.model_copy(update={"stage": f"{name}/{a.stage}" if a.stage else name})
                        for a in self.artifacts(c)
                    ]
                    continue
                out += [a.model_copy(update={"stage": name}) for a in self.source(c).list_artifacts(c.run_id)]
            except Exception as exc:
                log.warning("stage artifacts unavailable", stage=name, error=type(exc).__name__)
        terminal = self.get_run(r).status.terminal
        self._artifact_cache.put(str(r), out, self._ttl(r, terminal))
        if terminal and self._disk_ok(r):
            self.meta_cache.put(str(r), "artifacts", out)
        return out

    def record_summary(self, ref: RunRef | str, series: Sequence[MetricSeries]) -> None:
        """Final values of evaluation metrics (and the training loss) into the index — the Runs table's metric
        columns. Backends whose listing carries no metrics (Azure ML) get their columns this way."""
        out: dict[str, float] = {}
        for s in series:
            if s.key.split not in (Split.VAL, Split.TEST) and str(s.key) != "train/loss":
                continue
            _, v = s.finite()
            if v.size:
                out[str(s.key)] = float(v[-1])
            if len(out) >= 300:
                break
        try:
            self.index.update_summary(self.ref(ref), out)
        except Exception as exc:  # the columns are a convenience: never fail the load
            log.warning("summary metrics not recorded", error=type(exc).__name__)

    def is_cached(self, what: str, ref: RunRef | str) -> bool:
        """Is ``series`` / ``configs`` / ``artifacts`` of this run already in memory or on disk (no backend call)?"""
        r = self.ref(ref)
        k = str(r)
        lru = {"series": self._series_cache, "configs": self._config_cache, "artifacts": self._artifact_cache}[what]
        if k in lru:
            return True
        return bool(self.meta_cache is not None and self._disk_ok(r) and self.meta_cache.has(k, (what,)))

    def overview_cached(self, ref: RunRef | str, baseline: RunRef | str | None = None) -> dict[str, Any]:
        """The overview from what is already loaded — never a backend call for metrics, configs, files or the
        baseline. Each part says whether it is ``ready``; the run loader fills the rest in the background."""
        r = self.ref(ref)
        run = self.get_run(r)
        ready = {w: self.is_cached(w, r) for w in ("series", "configs", "artifacts")}
        series = self.series(r) if ready["series"] else []
        b = self.ref(baseline) if baseline else self.suggest_baseline(r)
        what = None
        if (
            b is not None
            and b != r
            and ready["series"]
            and ready["configs"]
            and self.is_cached("series", b)
            and self.is_cached("configs", b)
        ):
            try:
                what = self.compare(b, r)
            except NotFoundError:
                what = None
        return {
            "run": run,
            "ready": {**ready, "comparison": what is not None or b is None},
            "metric_keys": [str(s.key) for s in series],
            "summary": {str(s.key): s.aggregate() for s in series if s.key.split in (Split.VAL, Split.TEST)},
            "configs": self.configs(r) if ready["configs"] else [],
            "artifacts": self.artifacts(r) if ready["artifacts"] else [],
            "baseline": b,
            "what_changed": what,
            "findings": self.findings.for_runs([r]),
        }

    def _disk_ok(self, r: RunRef) -> bool:
        """The disk cache is used for finished runs of *remote* sources only: their metadata no longer changes,
        while a local folder can change at any time (and is fast to read anyway)."""
        if self.meta_cache is None or not getattr(self.sources.get(r.backend), "remote", True):
            return False
        try:
            return bool(self.get_run(r).status.terminal)
        except Exception:  # unknown run: let the normal path raise the real error
            return False

    def drop_cached(self, ref: RunRef | str) -> None:
        """Forget what is cached for a run and its pipeline stages (memory and disk) — the next read is cold."""
        r = self.ref(ref)
        keys = [str(r)]
        with contextlib.suppress(Exception):  # unknown run: nothing more to drop
            keys += [str(c) for c in self.get_run(r).children]
        for k in keys:
            for c in (self._series_cache, self._config_cache, self._artifact_cache, self._running_cache):
                c.drop(k)
            if self.meta_cache is not None:
                self.meta_cache.drop(k)

    def is_warm(self, ref: RunRef | str) -> bool:
        """Everything the run page needs is cached (memory or disk) — opening it does not wait for the backend."""
        r = self.ref(ref)
        k = str(r)
        mem = k in self._series_cache and k in self._config_cache and k in self._artifact_cache
        return mem or bool(self.meta_cache is not None and self._disk_ok(r) and self.meta_cache.has(k))

    def warm_up(
        self, ref: RunRef | str, force: bool = False, progress: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        """Metadata warm-up (not an artifact download): load and cache a run's metadata — run + pipeline stages, configs, metric series, file listing — so the
        run page opens without waiting. ``force`` drops what is cached first (re-reads from the backend)."""
        r = self.ref(ref)
        say = progress or (lambda _m: None)
        k = str(r)
        if force:
            self.drop_cached(r)
        say("run and pipeline stages")
        run = self.get_run(r)
        stages = self.stages(r)
        say("configs")
        configs = self.configs(r)
        say("metrics")
        series = self.series(r)
        self.record_summary(r, series)  # the Runs table's metric columns
        say("files")
        arts = self.artifacts(r)
        return {
            "run": k,
            "status": run.status.value,
            "stages": [n for n, _ in stages],
            "configs": len(configs),
            "metrics": len(series),
            "files": len(arts),
            "on_disk": bool(self._disk_ok(r) and self.meta_cache.has(k)),
        }

    def find_artifact(self, ref: RunRef | str, path: str) -> Artifact | None:
        """``path`` or ``<run ref>::<path>`` (a stage's file inside a pipeline)."""
        arts = self.artifacts(ref)
        if "::" in path:
            owner, _, rel = path.partition("::")
            return next((a for a in arts if str(a.run) == owner and a.path == rel), None)
        return next((a for a in arts if a.path == path), None)

    def download_plan(
        self,
        ref: RunRef | str,
        profile: DownloadProfile,
        stages: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
    ) -> list[Artifact]:
        arts = self.artifacts(ref)
        if stages:
            arts = [a for a in arts if (a.stage or "") in set(stages) or (not a.stage and "" in set(stages))]
        if paths:
            want = set(paths)
            return [a for a in arts if a.path in want or f"{a.run}::{a.path}" in want]
        return select(arts, profile)

    def plans(self, ref: RunRef | str, stages: Sequence[str] | None = None) -> dict[str, dict[str, Any]]:
        """Per profile: files, known bytes, files without a size, files already cached — shown before downloading."""
        out = {}
        for prof, spec in PROFILES.items():
            chosen = self.download_plan(ref, prof, stages)
            out[prof.value] = {
                "label": spec.label or prof.value,
                "help": spec.help,
                "files": len(chosen),
                "bytes": sum(a.size_bytes or 0 for a in chosen),
                "unknown_size": sum(1 for a in chosen if a.size_bytes is None),
                "cached": sum(1 for a in chosen if self.artifact_store.cached(a) is not None),
            }
        return out

    def read_log(self, ref: RunRef | str, name: str | None = None, tail_bytes: int | None = 64_000) -> str:
        """The run's most telling log (``run_logs.log_catalog``: a failed stage's, the training process's std_log —
        never a pipeline's orchestration log or a platform log first); ``name``: a file whose path ends with it."""
        from model_forensic.application.run_logs import log_catalog, read_log_file

        r = self.ref(ref)
        try:
            files = log_catalog(self, r)["files"]
        except Exception:  # listing failed: the backend's own choice
            files = []
        pick = next((f for f in files if name and f["path"].endswith(name)), None) if name else (files or [None])[0]
        if pick is None:
            try:
                return self.source(r).read_log(r.run_id, name, tail_bytes)
            except NotFoundError as exc:
                raise NotFoundError("no logs in the run or its stages") from exc
        got = read_log_file(self, r, pick["id"], tail_bytes)
        head = f"===== stage {pick['stage']} ({pick['run']}) · {pick['path']} =====\n" if pick["stage"] else ""
        return head + str(got["text"])

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
        stage: str | None = None,
    ) -> Comparison:
        """Deltas and config diff; ``stage``: what that pipeline stage logged, per run that has it (stages never mix)."""
        b, c = self.ref(baseline), self.ref(candidate)
        with telemetry.timed("compare"):
            reps = [self._stage_data(r, stage) for r in self.replicates(b)] if use_replicates else []
            return compute_comparison(
                self._stage_data(b, stage),
                self._stage_data(c, stage),
                self.diff_configs(b, c),
                self.metrics,
                reps,
                {Split(s) for s in splits},
            )

    def _stage_data(self, ref: RunRef, stage: str | None) -> RunData:
        """The run's data as one stage logged it (plain keys), when the run has that stage — fetching only that
        stage when the pipeline is not loaded yet; else the whole run."""
        if not stage or stage not in [n for n, _ in self.stages(ref)]:
            return self.run_data(ref)
        return RunData(run=self.get_run(ref), series={s.key: s for s in self.stage_series(ref, stage)})

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
        plugins = [self.analyses.get(n) for n in analyses] if analyses else self.analyses.all()
        need = {
            "with_tables": any(Requirement.EVAL_TABLES in pl.requires for pl in plugins),
            "with_evals": any(Requirement.EVAL_JSON in pl.requires for pl in plugins),
            "with_config": any(Requirement.CONFIG in pl.requires for pl in plugins),
            "with_lr": any(Requirement.LR in pl.requires for pl in plugins),
        }
        ctx = AnalysisContext(
            target=self._safe_run_data(t, **need),
            baseline=self._safe_run_data(b, **need) if b else None,
            baseline_replicates=[self.run_data(r) for r in self.replicates(b)] if b else [],
            config_diff=self.diff_configs(b, t) if b else None,
            registry=self.metrics,
        )
        results: list[AnalysisResult] = []
        for plugin in plugins:
            if Requirement.BASELINE in plugin.requires and b is None:
                results.append(AnalysisResult(analysis=plugin.name, skipped_reason="requires a baseline run"))
                continue
            p = plugin.Params.model_validate(self._policy_params(plugin, (params or {}).get(plugin.name, {})))
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

    def _policy_params(self, plugin: Any, given: dict[str, Any]) -> dict[str, Any]:
        """Team policy defaults (config/team.local.yaml) under the parameters given for this call."""
        fields = getattr(plugin.Params, "model_fields", {})
        base: dict[str, Any] = dict(self.team.analysis_params.get(plugin.name, {}))
        if "deprioritized" in fields and self.team.deprioritized_classes:
            base.setdefault("deprioritized", tuple(self.team.deprioritized_classes))
        if plugin.name == "metric_coverage" and self.team.expected_metrics:
            base.setdefault("expected", self.team.expected_metrics)
        return {**{k: v for k, v in base.items() if k in fields}, **given}

    def _safe_run_data(self, ref: RunRef, **need: bool) -> RunData:
        """Evaluation files and configs are optional evidence: a failure must not block the other analyses."""
        if not any(need.values()):
            return self.run_data(ref)
        parts: dict[str, Any] = {}
        for k, v in need.items():
            if not v:
                continue
            try:
                if k == "with_tables":
                    parts[k] = self.eval_tables(ref)
                elif k == "with_evals":
                    parts[k] = self.eval_json(ref)
                elif k == "with_lr":
                    parts[k] = self.lr_series(ref)
                else:
                    parts[k] = self.flat_config(ref)
            except Exception as exc:
                log.warning("optional evidence unavailable", run=str(ref), what=k, error=type(exc).__name__)
        base = self.run_data(ref)
        return RunData(
            run=base.run,
            series=base.series,
            tables=parts.get("with_tables", ()),
            evals=parts.get("with_evals", ()),
            configs=parts.get("with_config", {}),
            lr=parts.get("with_lr"),
        )

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
        stages: Sequence[str] | None = None,
    ) -> tuple[list[LocalArtifact], DownloadProgress]:
        r = self.ref(ref)
        chosen = self.download_plan(r, profile, stages, paths)
        with telemetry.timed("artifacts.download"):
            files, prog = self.artifact_store.fetch(self.source(r), chosen, cancel=cancel, on_progress=on_progress)
        telemetry.incr("artifacts.bytes", prog.done_bytes)
        telemetry.incr("artifacts.cache_hits", prog.cached_files)
        self.link_stages(r, files)
        self.write_manifest(r, files)
        return files, prog

    def rel_path(self, ref: RunRef | str, a: Artifact) -> str:
        """Where a file of ``ref`` is, relative to the run's local folder (``run_root``): its own files under their
        remote path, a pipeline stage's under ``stages/<stage>/`` (see ``link_stages``)."""
        return self.rel_path_of(ref, a.run, a.stage, a.path)

    def rel_path_of(self, ref: RunRef | str, run: RunRef | str, stage: str | None, path: str) -> str:
        """``rel_path`` from the parts (the file's run, its stage and its remote path)."""
        r, fr = self.ref(ref), self.ref(run)
        if fr == r:
            return path
        rel = f"stages/{_safe_name(stage or fr.run_id)}"
        if not (self.artifact_store.run_root(r) / rel).exists():  # no link (a file system without symlinks)
            return str(self.artifact_store.run_root(fr) / path)
        return f"{rel}/{path}"

    def link_stages(self, ref: RunRef | str, files: Sequence[LocalArtifact]) -> list[str]:
        """A pipeline's stages are runs of their own (cached under their run ids): link each stage's folder into the
        run's folder as ``stages/<stage>`` so every downloaded file sits under one root. Returns problems (a file
        system without symlinks): the files stay where they are, the paths say so."""
        r = self.ref(ref)
        root = self.artifact_store.run_root(r)
        problems: list[str] = []
        for a in {f.artifact.run: f.artifact for f in files if f.artifact.run != r}.values():
            link = root / "stages" / _safe_name(a.stage or a.run.run_id)
            target = self.artifact_store.run_root(a.run)
            try:
                if link.is_symlink() and link.resolve() == target.resolve():
                    continue
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.is_symlink():
                    link.unlink()
                if not link.exists():
                    import os

                    target.mkdir(parents=True, exist_ok=True)  # relative: the data folder can move
                    link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
            except OSError as exc:
                problems.append(f"{link}: {exc}")
        return problems

    def write_manifest(self, ref: RunRef, files: Sequence[LocalArtifact]) -> Path:
        """``download_manifest.json`` next to the run's local files: every local file traced to its remote source."""
        import json
        from datetime import datetime as dt
        from datetime import timezone

        path = self.artifact_store.run_root(ref) / "download_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                entries = {
                    f"{e['remote']['run']}::{e['remote']['path']}": e for e in json.loads(path.read_text())["files"]
                }
            except (ValueError, KeyError, TypeError):
                entries = {}
        now = dt.now(timezone.utc).isoformat()
        for f in files:
            a = f.artifact
            entries[f"{a.run}::{a.path}"] = {
                "remote": {"run": str(a.run), "stage": a.stage, "path": a.path},
                "local_path": str(f.local_path),
                "rel_path": self.rel_path(ref, a),  # under the run's folder (this manifest's folder)
                "sha256": f.sha256,
                "size_bytes": f.local_path.stat().st_size if f.local_path.exists() else a.size_bytes,
                "kind": a.kind.value,
                "from_cache": f.from_cache,
                "recorded_at": now,
            }
        doc = {
            "run": str(ref),
            "updated_at": now,
            "files": sorted(entries.values(), key=lambda e: (e["remote"]["run"], e["remote"]["path"])),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2))
        tmp.replace(path)
        return path

    def export_metrics(self, ref: RunRef | str) -> tuple[Path, Path]:
        """Every metric history of the run (stages merged) as JSON and long CSV, with the raw logged names."""
        import csv
        import json

        r = self.ref(ref)
        root = self.artifact_store.run_root(r) / "_metrics"
        root.mkdir(parents=True, exist_ok=True)
        series = self.series(r)
        doc = [
            {
                "key": str(s.key),
                "raw_name": s.provenance.get("raw_name"),
                "stage": s.provenance.get("stage"),
                "steps": list(s.steps),
                "values": [v if v == v else None for v in s.values],
            }
            for s in series
        ]
        jpath, cpath = root / "metrics.json", root / "metrics.csv"
        jpath.write_text(json.dumps({"run": str(r), "series": doc}))
        with cpath.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["key", "raw_name", "stage", "step", "value"])
            for s in series:
                for st, v in zip(s.steps, s.values, strict=True):
                    w.writerow(
                        [
                            str(s.key),
                            s.provenance.get("raw_name", ""),
                            s.provenance.get("stage", ""),
                            st,
                            "" if v != v else v,
                        ]
                    )
        return jpath, cpath


def flat_params(snapshots: Sequence[ConfigurationSnapshot]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in snapshots:
        out.update(flatten(s.data))
    return out


def _safe_name(x: str) -> str:
    """A folder name for a stage: letters, digits, ``._-`` (anything else becomes ``_``)."""
    import re

    return re.sub(r"[^A-Za-z0-9._-]+", "_", x).strip("._") or "stage"
