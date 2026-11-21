"""Application facade: orchestrates retrieval (ports) → computation (analysis) → persistence.

The API, CLI and AI tools all call this one service, so behaviour is identical across surfaces.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from model_forensic.analysis.base import AnalysisContext, AnalysisResult, Requirement, RunData
from model_forensic.analysis.comparison import compare as compute_comparison
from model_forensic.analysis.config_diff import ConfigDiffer, flatten
from model_forensic.analysis.metric_registry import MetricRegistry, default_registry
from model_forensic.analysis.registry import AnalysisRegistry, builtin_registry
from model_forensic.analysis.sequences import SequenceTable
from model_forensic.application.ports import (
    FindingStore,
    ListRunsQuery,
    RunSource,
    SearchFilters,
    SearchIndex,
    SearchResult,
)
from model_forensic.application.stages import merge_stage_series
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.comparison import Comparison
from model_forensic.domain.config import ConfigDiff, ConfigurationSnapshot
from model_forensic.domain.findings import Finding
from model_forensic.domain.metrics import MetricKey, MetricSeries, Split
from model_forensic.domain.run import Run, RunRef
from model_forensic.infrastructure.artifacts.store import (
    PROFILES,
    ArtifactStore,
    DownloadProfile,
    DownloadProgress,
    LocalArtifact,
    select,
)
from model_forensic.infrastructure.search.query_parser import looks_natural, parse_query, rewrite_natural_language
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
        artifacts: ArtifactStore,
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
        self.team = TeamPolicy()
        self.eval_tables_budget_bytes = 200 * 1024**2
        self.index = index
        self.findings = findings
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
                stages = self.stages(r)
                if stages:
                    all_series = merge_stage_series(
                        all_series, [(name, self.source(c).get_metric_series(c.run_id)) for name, c in stages]
                    )
            if self.get_run(r).status.terminal:
                self._series_cache.put(cache_key, all_series)
        if keys:
            want = set(keys)
            return [s for s in all_series if s.key in want]
        return all_series

    def stages(self, ref: RunRef | str) -> list[tuple[str, RunRef]]:
        """``(stage name, child ref)`` in pipeline order, for pipeline runs; empty otherwise."""
        r = self.ref(ref)
        run = self.get_run(r)
        if not run.children:
            return []
        named = dict(part.split("=", 1) for part in str(run.backend_extras.get("stages", "")).split(",") if "=" in part)
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

    def run_data(self, ref: RunRef | str, with_tables: bool = False) -> RunData:
        r = self.ref(ref)
        return RunData(
            run=self.get_run(r),
            series={s.key: s for s in self.series(r)},
            tables=self.eval_tables(r) if with_tables else (),
        )

    def eval_tables(self, ref: RunRef | str, stages: Sequence[str] | None = None) -> list[SequenceTable]:
        """Per-sequence evaluation parquets of the run (pipelines: every evaluation stage), downloaded once."""
        from model_forensic.analysis import sequences

        r = self.ref(ref)
        key = f"{r}|{','.join(stages or [])}"
        hit: list[SequenceTable] | None = self._tables_cache.get(key)
        if hit is not None:
            return hit
        plan = self.download_plan(r, DownloadProfile.EVAL_PARQUET, stages)
        if not plan:
            return []
        uncached = sum(a.size_bytes or 0 for a in plan if self.artifact_store.cached(a) is None)
        if uncached > self.eval_tables_budget_bytes:
            # Analyses download tables implicitly; anything larger is left to an explicit download.
            log.warning("evaluation tables over budget", run=str(r), bytes=uncached)
            return []
        files, _ = self.download(r, DownloadProfile.EVAL_PARQUET, stages=stages)
        order = {name: i for i, (name, _) in enumerate(self.stages(r))}
        files = sorted(files, key=lambda f: (order.get(f.artifact.stage or "", -1), f.artifact.path))
        out = [sequences.load(f.local_path, remote_path=f.artifact.path, stage=f.artifact.stage) for f in files]
        self._tables_cache.put(key, out)
        return out

    def configs(self, ref: RunRef | str) -> list[ConfigurationSnapshot]:
        r = self.ref(ref)
        hit: list[ConfigurationSnapshot] | None = self._config_cache.get(str(r))
        if hit is None:
            hit = self.source(r).get_configs(r.run_id)
            # Pipelines: the composed configs live in the stages (e.g. stage_output/summaries/train/composed_config.yaml).
            if not [c for c in hit if c.source != "params"]:
                for name, c in self.stages(r):
                    try:
                        hit += [
                            snap.model_copy(update={"source": f"{name}/{snap.source}"})
                            for snap in self.source(c).get_configs(c.run_id)
                        ]
                    except Exception as exc:
                        log.warning("stage config unavailable", stage=name, error=type(exc).__name__)
            self._config_cache.put(str(r), hit)
        return hit

    def flat_config(self, ref: RunRef | str) -> dict[str, Any]:
        return self.differ.merge(self.configs(ref))

    def artifacts(self, ref: RunRef | str) -> list[Artifact]:
        """The run's files; for pipelines also every stage's files (``Artifact.stage``, ``Artifact.run`` = stage)."""
        r = self.ref(ref)
        hit: list[Artifact] | None = self._artifact_cache.get(str(r))
        if hit is not None:
            return hit
        out = list(self.source(r).list_artifacts(r.run_id))
        for name, c in self.stages(r):
            try:
                out += [a.model_copy(update={"stage": name}) for a in self.source(c).list_artifacts(c.run_id)]
            except Exception as exc:
                log.warning("stage artifacts unavailable", stage=name, error=type(exc).__name__)
        if self.get_run(r).status.terminal:
            self._artifact_cache.put(str(r), out)
        return out

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
        """For pipelines without their own log: the failed stage's log, else the last stage's."""
        r = self.ref(ref)
        try:
            return self.source(r).read_log(r.run_id, name, tail_bytes)
        except NotFoundError:
            stages = self.stages(r)
            if not stages:
                raise
        ordered = sorted(stages, key=lambda st: self.get_run(st[1]).status.value != "failed")
        for stage, c in ordered[:1] + list(reversed(ordered[1:])):
            try:
                return f"===== stage {stage} ({c.run_id}) =====\n" + self.source(c).read_log(c.run_id, name, tail_bytes)
            except NotFoundError:
                continue
        raise NotFoundError("no logs in the pipeline or its stages")

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
        plugins = [self.analyses.get(n) for n in analyses] if analyses else self.analyses.all()
        tables = any(Requirement.EVAL_TABLES in pl.requires for pl in plugins)
        ctx = AnalysisContext(
            target=self._safe_run_data(t, tables),
            baseline=self._safe_run_data(b, tables) if b else None,
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

    def _safe_run_data(self, ref: RunRef, with_tables: bool) -> RunData:
        """Evaluation tables are optional evidence: a failed download must not block the other analyses."""
        if not with_tables:
            return self.run_data(ref)
        try:
            return self.run_data(ref, with_tables=True)
        except Exception as exc:
            log.warning("evaluation tables unavailable", run=str(ref), error=type(exc).__name__)
            return self.run_data(ref)

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
        self.write_manifest(r, files)
        return files, prog

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
