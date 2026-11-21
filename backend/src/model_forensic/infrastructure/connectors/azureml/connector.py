"""Azure ML connector.

Job metadata comes from the raw ARM jobs operations (entity client as fallback); pipeline stages, metric
histories and run files from the run-history data plane and the default datastore (``runhistory.py``, ported
from the field-tested v1 tool). MLflow tracking remains available as the metric/artifact source
(``metrics_api: mlflow``) and is used automatically when no run-history gateway is configured. All SDK calls
are wrapped with bounded retry and a client-side rate limiter, and every object is converted to domain types
at this boundary. Raw metric names are mapped to canonical keys by ``connectors.naming`` (xflow layout aware).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import timezone
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from model_forensic.application.ports import ListRunsQuery, Page
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.run import Run, RunRef
from model_forensic.infrastructure.connectors.azureml.mapper import map_job
from model_forensic.infrastructure.connectors.azureml.runhistory import (
    STAGE_OUTPUT,
    LazyJobs,
    RunHistoryGateway,
    fast_jobs,
    make_credential,
)
from model_forensic.infrastructure.connectors.local.connector import classify_artifact
from model_forensic.infrastructure.connectors.naming import NameMapper
from model_forensic.shared.errors import BackendUnavailableError, NotFoundError
from model_forensic.shared.logging import get_logger
from model_forensic.shared.retry import RetryPolicy, retry

log = get_logger(__name__)
T = TypeVar("T")


class RateLimiter:
    """Simple token bucket; Azure ML management APIs throttle aggressively."""

    def __init__(self, rate_per_s: float = 8.0, burst: int = 16) -> None:
        self.rate, self.capacity = rate_per_s, burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


class AzureMLRunSource:
    def __init__(
        self,
        name: str,
        ml_client: Any,
        mlflow_client: Any = None,
        *,
        history: RunHistoryGateway | None = None,
        jobs: Any = None,
        rate: RateLimiter | None = None,
        policy: RetryPolicy | None = None,
        sync_max_jobs: int = 1000,
    ) -> None:
        self.name = name
        self._ml = ml_client
        self._jobs = jobs if jobs is not None else LazyJobs(lambda: ml_client.jobs)
        self._mlflow = mlflow_client
        self._history = history if mlflow_client is None or history is not None else None
        if self._history is None and self._mlflow is None:
            raise ValueError("AzureMLRunSource needs a run-history gateway or an MLflow client")
        self._rate = rate or RateLimiter()
        self._policy = policy or RetryPolicy()
        self._experiments: dict[str, str] = {}
        self._pager: tuple[int, Any] | None = None
        self.sync_max_jobs = sync_max_jobs

    @classmethod
    def from_options(cls, o: dict[str, Any]) -> AzureMLRunSource:
        try:
            from azure.ai.ml import MLClient
        except ImportError as exc:
            raise BackendUnavailableError(
                "Azure ML support not installed: pip install 'model-forensic[azure]'"
            ) from exc
        cred = make_credential(o.get("credential", "default" if o.get("interactive") else "cli"), o.get("tenant_id"))
        sub, rg, ws = o["subscription_id"], o["resource_group"], o["workspace"]
        ml = MLClient(cred, sub, rg, ws)
        jobs = LazyJobs(lambda: fast_jobs(ml, rg, ws) if o.get("fast_listing", True) else ml.jobs)
        if o.get("metrics_api", "runhistory") == "mlflow":
            import mlflow

            mlflow.set_tracking_uri(ml.workspaces.get(ws).mlflow_tracking_uri)
            return cls(
                name=o.get("name", "azureml"),
                ml_client=ml,
                mlflow_client=mlflow.MlflowClient(),
                jobs=jobs,
                sync_max_jobs=int(o.get("sync_max_jobs", 1000)),
            )
        return cls(
            name=o.get("name", "azureml"),
            ml_client=ml,
            history=RunHistoryGateway(ml, sub, rg, ws),
            jobs=jobs,
            sync_max_jobs=int(o.get("sync_max_jobs", 1000)),
        )

    def _call(self, fn: Callable[[], T], op: str) -> T:
        def wrapped() -> T:
            self._rate.acquire()
            t0 = time.perf_counter()
            try:
                return fn()
            except Exception as exc:
                if _is_not_found(exc):
                    raise NotFoundError(f"{op}: not found") from exc
                if _is_transient(exc):
                    raise BackendUnavailableError(f"{op}: {type(exc).__name__}") from exc
                raise
            finally:
                log.debug("azureml call", op=op, latency_ms=round((time.perf_counter() - t0) * 1000, 1))

        return retry(
            wrapped, self._policy, on_retry=lambda n, e: log.warning("azureml retry", op=op, attempt=n, error=str(e))
        )

    # Configs need several calls per run on Azure; they are fetched on demand, not during sync.
    index_configs_on_sync = False

    def list_runs(self, query: ListRunsQuery) -> Page:
        """Newest first. The SDK pager is kept between pages (no re-listing from the start); a sync stops after
        ``sync_max_jobs`` jobs or at the first job older than ``updated_since``."""
        start = int(query.cursor or 0)

        def fetch() -> list[Any]:
            state = self._pager
            if state is None or state[0] != start:
                it = iter(self._jobs.list())
                for _ in range(start):
                    if next(it, None) is None:
                        break
                state = (start, it)
            out: list[Any] = []
            it = state[1]
            while len(out) < query.page_size:
                j = next(it, None)
                if j is None:
                    break
                out.append(j)
            self._pager = (start + len(out), it)
            return out

        jobs = self._call(fetch, "jobs.list")
        runs = [map_job(j, self.name) for j in jobs]
        stop = len(jobs) < query.page_size or start + len(jobs) >= self.sync_max_jobs
        if query.updated_since is not None:
            since = (
                query.updated_since if query.updated_since.tzinfo else query.updated_since.replace(tzinfo=timezone.utc)
            )
            fresh = [r for r in runs if r.created_at is None or r.created_at >= since]
            stop = stop or len(fresh) < len(runs)
            runs = fresh
        if query.experiment:
            runs = [r for r in runs if r.experiment == query.experiment]
        if stop:
            self._pager = None
        return Page(items=runs, next_cursor=None if stop else str(start + len(jobs)))

    def get_run(self, run_id: str) -> Run:
        job = self._call(lambda: self._jobs.get(run_id), "jobs.get")
        if self._history is not None:
            run = map_job(job, self.name)
            if run.experiment:
                self._experiments[run_id] = run.experiment
            if run.kind.value == "pipeline" and run.experiment:
                exp = run.experiment
                try:
                    kids = self._call(lambda: self._history.list_children(exp, run_id), "runhistory.children")  # type: ignore[union-attr]
                except NotFoundError:
                    kids = []
                for kid, _stage in kids:
                    self._experiments[kid] = exp
                stages = {kid: stage for kid, stage in kids}
                run = run.model_copy(
                    update={
                        "children": tuple(RunRef(backend=self.name, run_id=k) for k in stages),
                        "backend_extras": {
                            **run.backend_extras,
                            "stages": ",".join(f"{k}={v}" for k, v in stages.items()),
                        },
                    }
                )
            return run
        props: dict[str, str] = {}
        try:
            mr = self._call(lambda: self._mlflow.get_run(run_id), "mlflow.get_run")
            props = {**mr.data.tags}
            run = map_job(job, self.name, props)
            return run.model_copy(update={"summary_metrics": {k: float(v) for k, v in mr.data.metrics.items()}})
        except NotFoundError:
            return map_job(job, self.name, props)

    def _raw_metric_names(self, run_id: str) -> list[str]:
        if self._history is not None:
            h = self._history
            return self._call(lambda: h.list_metric_names(run_id), "runhistory.list_metric")
        mr = self._call(lambda: self._mlflow.get_run(run_id), "mlflow.get_run")
        return list(mr.data.metrics)

    def list_metric_keys(self, run_id: str) -> list[MetricKey]:
        names = NameMapper()
        return [names(raw) for raw in self._raw_metric_names(run_id)]

    def get_metric_series(self, run_id: str, keys: Sequence[MetricKey] | None = None) -> list[MetricSeries]:
        names = NameMapper()
        wanted = set(keys) if keys else None
        out = []
        for raw in self._raw_metric_names(run_id):
            key = names(raw)
            if wanted is not None and key not in wanted:
                continue
            if self._history is not None:
                h = self._history
                steps, values = self._call(partial(h.metric_history, run_id, raw), "runhistory.get_sampled_metric")
                order = sorted(range(len(steps)), key=lambda i: steps[i])
                series = MetricSeries(
                    key=key,
                    steps=tuple(float(steps[i]) for i in order),
                    values=tuple(float(values[i]) for i in order),
                    source="azureml.runhistory",
                    provenance={"backend": self.name, "run_id": run_id, "raw_name": raw},
                )
            else:
                hist: list[Any] = self._call(
                    partial(self._mlflow.get_metric_history, run_id, raw), "mlflow.get_metric_history"
                )
                hist = sorted(hist, key=lambda m: (m.step, m.timestamp))
                series = MetricSeries(
                    key=key,
                    steps=tuple(float(m.step) for m in hist),
                    values=tuple(float(m.value) for m in hist),
                    timestamps=tuple(m.timestamp / 1000 for m in hist),
                    source="mlflow",
                    provenance={"backend": self.name, "run_id": run_id, "raw_name": raw},
                )
            out.append(series)
        return out

    def get_configs(self, run_id: str) -> list[ConfigurationSnapshot]:
        run = self.get_run(run_id)
        ref = RunRef(backend=self.name, run_id=run_id)
        snaps = [ConfigurationSnapshot(run=ref, source="params", data=dict(run.params))] if run.params else []
        for art in self.list_artifacts(run_id):
            if art.path.endswith(("config.yaml", "config.json")) and (art.size_bytes or 0) < 2_000_000:
                # e.g. stage_output/summaries/train/composed_config.yaml (Hydra-composed pipeline config)
                import json

                import yaml

                text = b"".join(self.open_artifact(run_id, art.path)).decode("utf-8", errors="replace")
                data = json.loads(text) if art.path.endswith(".json") else yaml.safe_load(text)
                if isinstance(data, dict):
                    snaps.append(ConfigurationSnapshot(run=ref, source=Path(art.path).name, data=data))
        return snaps

    def _experiment(self, run_id: str) -> str:
        if run_id not in self._experiments:
            run = self.get_run(run_id)
            self._experiments[run_id] = run.experiment or ""
        return self._experiments[run_id]

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        ref = RunRef(backend=self.name, run_id=run_id)
        if self._history is not None:
            h, exp = self._history, self._experiment(run_id)
            files = self._call(lambda: h.list_files(exp, run_id), "runhistory.list_files")
            return sorted(
                (
                    Artifact(run=ref, path=f.path, kind=classify_artifact(f.path), size_bytes=f.size_bytes)
                    for f in files
                ),
                key=lambda a: a.path,
            )
        out: list[Artifact] = []
        stack: list[str | None] = [None]
        while stack:
            path = stack.pop()
            listing: list[Any] = self._call(partial(self._mlflow.list_artifacts, run_id, path), "mlflow.list_artifacts")
            for fi in listing:
                if fi.is_dir:
                    stack.append(fi.path)
                else:
                    out.append(
                        Artifact(run=ref, path=fi.path, kind=classify_artifact(fi.path), size_bytes=fi.file_size)
                    )
        return sorted(out, key=lambda a: a.path)

    def open_artifact(self, run_id: str, path: str, offset: int = 0) -> Iterator[bytes]:
        # MLflow's artifact API downloads whole files; resumability is provided by the artifact
        # store for multi-GB checkpoints via direct blob access (see architecture §11).
        if self._history is not None:
            h, exp = self._history, self._experiment(run_id)
            data = self._call(lambda: h.download(exp, run_id, path), "runhistory.download")
            for i in range(offset, len(data), 1 << 20):
                yield data[i : i + (1 << 20)]
            return
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            local = self._call(lambda: self._mlflow.download_artifacts(run_id, path, tmp), "mlflow.download_artifacts")
            with open(local, "rb") as fh:  # noqa: PTH123
                fh.seek(offset)
                while chunk := fh.read(1 << 20):
                    yield chunk

    def read_log(self, run_id: str, name: str | None = None, tail_bytes: int | None = None) -> str:
        logs = [
            a
            for a in self.list_artifacts(run_id)
            if a.path.startswith(("user_logs/", "azureml-logs/", "logs/", "system_logs/", f"{STAGE_OUTPUT}/logs/"))
            and a.kind.value == "log"
        ] or [a for a in self.list_artifacts(run_id) if a.path.startswith(("user_logs/", "azureml-logs/", "logs/"))]
        if not logs:
            raise NotFoundError("no logs for run")
        target = next((a for a in logs if name and a.path.endswith(name)), None) or next(
            (a for a in logs if "std_log" in a.path), logs[0]
        )
        data = b"".join(self.open_artifact(run_id, target.path))
        if tail_bytes:
            data = data[-tail_bytes:]
        return data.decode("utf-8", errors="replace")


def _is_not_found(exc: Exception) -> bool:
    n = type(exc).__name__
    return (
        n in {"ResourceNotFoundError", "RestException"} and "not found" in str(exc).lower()
    ) or n == "ResourceNotFoundError"


def _is_transient(exc: Exception) -> bool:
    n = type(exc).__name__
    s = str(exc).lower()
    return n in {
        "ServiceRequestError",
        "ServiceResponseError",
        "HttpResponseError",
        "ConnectionError",
        "TimeoutError",
    } and ("429" in s or "503" in s or "502" in s or "timeout" in s or "throttl" in s or n != "HttpResponseError")
