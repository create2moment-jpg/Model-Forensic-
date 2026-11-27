"""Azure ML run-history data plane: pipeline stages, metric histories and run files.

Ported from the field-tested v1 Model Forensics tool, which profiled these paths against a busy workspace:

* **Credential**: ``AzureCliCredential`` first. ``DefaultAzureCredential`` probes the managed-identity endpoint
  before the CLI and stalled ~5 minutes where IMDS is unreachable (0.3 s with the CLI credential).
* **Job listing**: the raw ARM ``jobs`` operations (one HTTP call per page, records carry
  ``system_data.created_by/created_at``). The entity client resolves nested assets per job (~35 s/job measured).
* **Metrics**: the run-history metrics API (``list_metric`` with continuation tokens + ``get_sampled_metric``).
* **Files**: run-history artifacts (logs, default outputs) through per-file SAS URIs, and each stage's named
  outputs, which pipelines write to ``azureml/<run_id>/stage_output/`` in the workspace's default datastore.

Everything here is read-only. SDK objects are only touched in this module and the connector.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

ARTIFACT_URI_API_VERSION = "2024-07-01-preview"
STAGE_OUTPUT = "stage_output"


@dataclass(frozen=True)
class RunFile:
    run_id: str
    path: str  # stage outputs are prefixed with "stage_output/"
    size_bytes: int | None = None
    last_modified: str | None = None


def make_credential(mode: str = "cli", tenant_id: str | None = None) -> Any:
    """``cli`` (default): Azure CLI first, then the default chain without browser prompts.
    ``default``: the full default chain. ``managed_identity``: managed identity only (Azure-hosted)."""
    from azure.identity import (
        AzureCliCredential,
        ChainedTokenCredential,
        DefaultAzureCredential,
        ManagedIdentityCredential,
    )

    if mode == "managed_identity":
        return ManagedIdentityCredential()
    if mode == "default":
        return DefaultAzureCredential(exclude_interactive_browser_credential=True)
    cli = AzureCliCredential(tenant_id=tenant_id) if tenant_id else AzureCliCredential()
    return ChainedTokenCredential(cli, DefaultAzureCredential(exclude_interactive_browser_credential=True))


class RawARMJobs:
    """Read-only adapter over the generated ARM jobs operations with the workspace scope baked in."""

    def __init__(self, operations: Any, resource_group: str, workspace: str) -> None:
        self._ops, self._rg, self._ws = operations, resource_group, workspace

    def list(self, job_type: str | None = None, **_: Any) -> Iterable[Any]:
        jobs: Iterable[Any] = self._ops.list(self._rg, self._ws, list_view_type="ActiveOnly", job_type=job_type)
        return jobs

    def get(self, name: str) -> Any:
        return self._ops.get(self._rg, self._ws, name)


def fast_jobs(ml_client: Any, resource_group: str, workspace: str) -> Any:
    """The raw ARM jobs surface when this SDK version exposes it, else the entity client.

    ``MLClient._service_client_01_2024_preview_arm`` is built in the MLClient constructor without network calls
    (``MLClient.jobs`` resolves the workspace's endpoints on first access, which needs the network).
    """
    svc = getattr(ml_client, "_service_client_01_2024_preview_arm", None)
    raw = getattr(svc, "jobs", None)
    if raw is None:
        raw = getattr(getattr(ml_client.jobs, "service_client_01_2024_preview_arm", None), "jobs", None)
    return RawARMJobs(raw, resource_group, workspace) if raw is not None else ml_client.jobs


class LazyJobs:
    """Defers choosing/creating the jobs client to the first call, so building the app makes no Azure calls."""

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self._jobs: Any = None

    def _get(self) -> Any:
        if self._jobs is None:
            self._jobs = self._factory()
        return self._jobs

    def list(self, **kw: Any) -> Iterable[Any]:
        jobs: Iterable[Any] = self._get().list(**kw)
        return jobs

    def get(self, name: str) -> Any:
        return self._get().get(name)


class RunHistoryGateway:
    def __init__(
        self,
        ml_client: Any,
        subscription_id: str,
        resource_group: str,
        workspace: str,
        history: Any = None,
        container: Any = None,
        full_fidelity: bool = False,
    ) -> None:
        self._ml = ml_client
        self._scope = (subscription_id, resource_group, workspace)
        self._history_client = history
        self._container = container
        # Default = the sampled-metric API the v1 tool used on the real workspace; full fidelity is opt-in.
        self.full_fidelity = full_fidelity

    # ------------------------------------------------------------------ clients
    def _history(self) -> Any:
        if self._history_client is None:
            from azure.ai.ml._restclient.runhistory import RunHistoryClient

            jobs = self._ml.jobs
            self._history_client = RunHistoryClient(
                jobs._credential, base_url=jobs._api_url, **jobs._service_client_kwargs
            )
        return self._history_client

    def _default_container(self) -> Any:
        if self._container is None:
            from azure.storage.blob import BlobServiceClient

            ds = self._ml.datastores.get_default()
            service = BlobServiceClient(
                f"https://{ds.account_name}.blob.core.windows.net", credential=self._ml.jobs._credential
            )
            self._container = service.get_container_client(ds.container_name)
        return self._container

    # ------------------------------------------------------------------ stages
    def list_child_runs(self, experiment: str, run_id: str) -> list[Mapping[str, Any]]:
        """Each pipeline step as the run-history API returns it (``runId``, ``displayName``, ``status``, times, …)."""
        return [c for c in self._history().runs.get_child_by_experiment_name(*self._scope, experiment, run_id)]

    # ------------------------------------------------------------------ times
    def run_times(self, experiment: str, run_ids: Iterable[str], max_runs: int = 500) -> dict[str, Any]:
        """Created / start / end time of these runs, as studio shows them: ONE paged run-history query for the
        experiment (newest first), stopped as soon as every asked run was seen or after ``max_runs`` — never one call
        per run. The job resource often lacks the times (pipelines, jobs that never logged through MLflow)."""
        from azure.ai.ml._restclient.runhistory.models import QueryParams

        want = {str(r) for r in run_ids}
        out: dict[str, Any] = {}
        body = QueryParams(order_by="CreatedUtc desc", top=100)
        for i, r in enumerate(
            self._history().runs.get_by_query_by_experiment_name(*self._scope, experiment, body=body)
        ):
            rid = r.get("runId") if isinstance(r, dict) else getattr(r, "run_id", None)
            if rid in want:
                out[str(rid)] = r
                if len(out) == len(want):
                    break
            if i + 1 >= max_runs:
                break
        return out

    def run_time(self, run_id: str) -> Any:
        """One run's run-history record (times, status) — for a run opened before any sync read its times."""
        return self._history().runs.get(*self._scope, run_id)

    def list_children(self, experiment: str, run_id: str) -> list[tuple[str, str]]:
        """``(child_run_id, stage_name)`` for each pipeline step."""
        return [(str(c["runId"]), stage_name(c)) for c in self.list_child_runs(experiment, run_id)]

    # ------------------------------------------------------------------ metrics
    def list_metric_names(self, run_id: str) -> list[str]:
        names: list[str] = []
        body: dict[str, Any] = {}
        while True:
            resp = self._history().metrics.list_metric(*self._scope, run_id, body=body)
            names.extend(str(item["metricKey"]["name"]) for item in resp.value)
            token = resp.get("continuationToken") if hasattr(resp, "get") else getattr(resp, "continuation_token", None)
            if not token:
                return names
            body = {"continuationToken": token}

    def metric_history(self, run_id: str, name: str) -> tuple[list[float], list[float]]:
        """``(steps, values)``: the sampled-metric API (as the v1 tool on the real workspace); with ``full_fidelity``
        every logged point first, falling back to the sampled API when that yields nothing usable."""
        if self.full_fidelity:
            try:
                rows: list[Any] = []
                body: dict[str, Any] = {"metricName": name}
                while True:
                    resp = self._history().metrics.get_full_fidelity_metric(*self._scope, run_id, body=body)
                    rows.extend(resp.get("value") or [])
                    token = resp.get("continuationToken")
                    if not token:
                        break
                    body = {"metricName": name, "continuationToken": token}
                steps, values = _parse_rows(rows)
                if steps:
                    return steps, values
            except Exception as exc:  # unsupported on this workspace/api-version → sampled values
                if type(exc).__name__ in {"ClientAuthenticationError", "ServiceRequestError"}:
                    raise
        sample = self._history().metrics.get_sampled_metric(
            *self._scope, run_id, body={"metricName": name, "primaryAggregator": "average", "compact": False}
        )
        return _parse_rows(list(sample.get("value") or []))

    # ------------------------------------------------------------------ files
    def list_files(self, experiment: str, run_id: str) -> list[RunFile]:
        files = [
            RunFile(run_id=run_id, path=str(a["path"]))
            for a in self._history().run_artifacts.list_in_container_by_experiment_name(
                *self._scope, experiment, run_id
            )
        ]
        prefix = f"azureml/{run_id}/{STAGE_OUTPUT}/"
        for blob in self._default_container().list_blobs(name_starts_with=prefix):
            rel = str(blob.name)[len(prefix) :]
            if not rel or not blob.size:  # directory markers
                continue
            modified = getattr(blob, "last_modified", None)
            files.append(
                RunFile(
                    run_id=run_id,
                    path=f"{STAGE_OUTPUT}/{rel}",
                    size_bytes=int(blob.size),
                    last_modified=modified.isoformat() if modified else None,
                )
            )
        return files

    def download(
        self, experiment: str, run_id: str, path: str, offset: int = 0, tail_bytes: int | None = None
    ) -> bytes:
        """The file from ``offset``; ``tail_bytes``: only its last bytes (a ranged read: a large log's tail without
        downloading the whole file). A store that ignores the range still returns the right bytes (sliced here)."""
        if path.startswith(f"{STAGE_OUTPUT}/"):
            blob = f"azureml/{run_id}/{path}"
            cont = self._default_container()
            if tail_bytes:
                size = int(cont.get_blob_client(blob).get_blob_properties().size)
                offset = max(offset, size - tail_bytes)
            if offset:
                return bytes(cont.download_blob(blob, offset=offset).readall())
            return bytes(cont.download_blob(blob).readall())
        h = self._history()
        sub, rg, ws = self._scope
        url = (
            f"{h._base_url}/history/v1.0/subscriptions/{sub}/resourceGroups/{rg}/providers/"
            f"Microsoft.MachineLearningServices/workspaces/{ws}/experiments/{quote(experiment, safe='')}"
            f"/runs/{run_id}/artifacts/artifacturi?path={quote(path, safe='/')}&api-version={ARTIFACT_URI_API_VERSION}"
        )
        sas = h.send_request(_get_request(url)).text().strip().strip('"')
        if not sas.startswith("https://"):
            raise ValueError("unexpected artifact URI returned by Azure ML")
        # The SAS URL is issued by Azure ML for this workspace's storage account (no user-controlled host).
        if tail_bytes:
            try:
                with urlopen(Request(sas, method="HEAD"), timeout=60) as head:  # noqa: S310
                    size = int(head.headers.get("Content-Length") or 0)
                offset = max(offset, size - tail_bytes)
            except Exception:  # no length → the whole file, cut below
                size = 0
        if not offset:
            with urlopen(sas, timeout=600) as resp:  # noqa: S310
                data = bytes(resp.read())
            return data[-tail_bytes:] if tail_bytes else data
        with urlopen(Request(sas, headers={"Range": f"bytes={offset}-"}), timeout=600) as resp:  # noqa: S310
            ranged = getattr(resp, "status", 206) == 206
            data = bytes(resp.read())
        return data if ranged else data[offset:]


def _parse_rows(rows: list[Any]) -> tuple[list[float], list[float]]:
    steps: list[float] = []
    values: list[float] = []
    for row in rows:
        parsed = parse_metric_row(row)
        if parsed is not None:
            steps.append(parsed[0])
            values.append(parsed[1])
    return steps, values


def _get_request(url: str) -> Any:
    try:
        from azure.core.rest import HttpRequest
    except ImportError:  # only test doubles reach this without the SDK
        from types import SimpleNamespace

        return SimpleNamespace(method="GET", url=url)
    return HttpRequest("GET", url)


def stage_name(child: Mapping[str, Any]) -> str:
    """Display name, then the pipeline module name, then the run id's last segment."""
    if child.get("displayName"):
        return str(child["displayName"])
    module = (child.get("properties") or {}).get("azureml.moduleName")
    if module:
        return str(module)
    return PurePosixPath(str(child["runId"])).name


def parse_metric_row(row: Any) -> tuple[float, float] | None:
    """``(step, value)`` from a sampled-metric row (``MetricV2Value`` mapping or its dict repr string)."""
    if isinstance(row, str):
        try:
            row = ast.literal_eval(row)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(row, Mapping):
        return None
    data = row.get("data") or {}
    if not data:
        return None
    try:
        value = float(next(iter(data.values())))
        step = row.get("step")
        return (float(step) if step is not None else -1.0), value
    except (TypeError, ValueError, StopIteration):
        return None
