"""Azure ML connector (SDK v2 + MLflow tracking).

Job metadata comes from ``azure.ai.ml.MLClient.jobs``; metrics and artifacts from Azure ML's
MLflow-compatible tracking server. All SDK calls are wrapped with bounded retry and a client-side
rate limiter, and every object is converted to domain types at this boundary.

Status: implemented against the public SDK surface but NOT yet validated against a live
workspace in CI; see docs/architecture.md §10 for the validation plan.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from model_forensic.application.ports import ListRunsQuery, Page
from model_forensic.domain.artifacts import Artifact
from model_forensic.domain.config import ConfigurationSnapshot
from model_forensic.domain.metrics import MetricKey, MetricSeries, Split
from model_forensic.domain.run import Run, RunRef
from model_forensic.infrastructure.connectors.azureml.mapper import map_job
from model_forensic.infrastructure.connectors.local.connector import classify_artifact
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


def split_from_name(name: str) -> tuple[Split, str]:
    for prefix, split in (("train/", Split.TRAIN), ("val/", Split.VAL), ("eval/", Split.VAL), ("test/", Split.TEST)):
        if name.startswith(prefix):
            return split, name[len(prefix) :]
    return Split.UNKNOWN, name


class AzureMLRunSource:
    def __init__(
        self,
        name: str,
        ml_client: Any,
        mlflow_client: Any,
        *,
        rate: RateLimiter | None = None,
        policy: RetryPolicy | None = None,
    ) -> None:
        self.name = name
        self._ml = ml_client
        self._mlflow = mlflow_client
        self._rate = rate or RateLimiter()
        self._policy = policy or RetryPolicy()

    @classmethod
    def from_options(cls, o: dict[str, Any]) -> AzureMLRunSource:
        try:
            import mlflow
            from azure.ai.ml import MLClient
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise BackendUnavailableError(
                "Azure ML support not installed: pip install 'model-forensic[azure]'"
            ) from exc
        cred = DefaultAzureCredential(exclude_interactive_browser_credential=not o.get("interactive", False))
        ml = MLClient(cred, o["subscription_id"], o["resource_group"], o["workspace"])
        ws = ml.workspaces.get(o["workspace"])
        mlflow.set_tracking_uri(ws.mlflow_tracking_uri)
        return cls(name=o.get("name", "azureml"), ml_client=ml, mlflow_client=mlflow.MlflowClient())

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

    def list_runs(self, query: ListRunsQuery) -> Page:
        # The SDK pager is lazy; we materialize one page worth and keep a numeric cursor.
        def fetch() -> list[Any]:
            jobs = self._ml.jobs.list(parent_job_name=None)
            start = int(query.cursor or 0)
            out: list[Any] = []
            for i, j in enumerate(jobs):
                if i < start:
                    continue
                if len(out) >= query.page_size:
                    break
                out.append(j)
            return out

        jobs = self._call(fetch, "jobs.list")
        runs = [map_job(j, self.name) for j in jobs]
        if query.experiment:
            runs = [r for r in runs if r.experiment == query.experiment]
        nxt = str(int(query.cursor or 0) + len(jobs)) if len(jobs) == query.page_size else None
        return Page(items=runs, next_cursor=nxt)

    def get_run(self, run_id: str) -> Run:
        job = self._call(lambda: self._ml.jobs.get(run_id), "jobs.get")
        props: dict[str, str] = {}
        try:
            mr = self._call(lambda: self._mlflow.get_run(run_id), "mlflow.get_run")
            props = {**mr.data.tags}
            run = map_job(job, self.name, props)
            return run.model_copy(update={"summary_metrics": {k: float(v) for k, v in mr.data.metrics.items()}})
        except NotFoundError:
            return map_job(job, self.name, props)

    def list_metric_keys(self, run_id: str) -> list[MetricKey]:
        mr = self._call(lambda: self._mlflow.get_run(run_id), "mlflow.get_run")
        keys = []
        for raw in mr.data.metrics:
            split, rest = split_from_name(raw)
            k = MetricKey.parse(rest)
            keys.append(MetricKey(name=k.name, split=split, dims=k.dims))
        return keys

    def get_metric_series(self, run_id: str, keys: Sequence[MetricKey] | None = None) -> list[MetricSeries]:
        mr = self._call(lambda: self._mlflow.get_run(run_id), "mlflow.get_run")
        wanted = {str(k) for k in keys} if keys else None
        out = []
        for raw in mr.data.metrics:
            split, rest = split_from_name(raw)
            k0 = MetricKey.parse(rest)
            key = MetricKey(name=k0.name, split=split, dims=k0.dims)
            if wanted is not None and str(key) not in wanted:
                continue
            hist: list[Any] = self._call(
                partial(self._mlflow.get_metric_history, run_id, raw), "mlflow.get_metric_history"
            )
            hist = sorted(hist, key=lambda m: (m.step, m.timestamp))
            out.append(
                MetricSeries(
                    key=key,
                    steps=tuple(float(m.step) for m in hist),
                    values=tuple(float(m.value) for m in hist),
                    timestamps=tuple(m.timestamp / 1000 for m in hist),
                    source="mlflow",
                    provenance={"backend": self.name, "run_id": run_id, "raw_name": raw},
                )
            )
        return out

    def get_configs(self, run_id: str) -> list[ConfigurationSnapshot]:
        run = self.get_run(run_id)
        ref = RunRef(backend=self.name, run_id=run_id)
        snaps = [ConfigurationSnapshot(run=ref, source="params", data=dict(run.params))] if run.params else []
        for art in self.list_artifacts(run_id):
            if art.path.endswith(("config.yaml", "config.json")) and (art.size_bytes or 0) < 2_000_000:
                import json

                import yaml

                text = b"".join(self.open_artifact(run_id, art.path)).decode("utf-8", errors="replace")
                data = json.loads(text) if art.path.endswith(".json") else yaml.safe_load(text)
                if isinstance(data, dict):
                    snaps.append(ConfigurationSnapshot(run=ref, source=Path(art.path).name, data=data))
        return snaps

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        ref = RunRef(backend=self.name, run_id=run_id)
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
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            local = self._call(lambda: self._mlflow.download_artifacts(run_id, path, tmp), "mlflow.download_artifacts")
            with open(local, "rb") as fh:  # noqa: PTH123
                fh.seek(offset)
                while chunk := fh.read(1 << 20):
                    yield chunk

    def read_log(self, run_id: str, name: str | None = None, tail_bytes: int | None = None) -> str:
        logs = [a for a in self.list_artifacts(run_id) if a.path.startswith(("user_logs/", "azureml-logs/", "logs/"))]
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
