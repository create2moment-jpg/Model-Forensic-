"""Pure mapping from Azure ML SDK v2 entities to the domain model.

Kept free of SDK imports (duck-typed via ``getattr``) so it is unit-testable with plain
objects and survives minor SDK attribute changes. This is the ONLY place that knows Azure's
shape of a job.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from model_forensic.domain.run import (
    ComputeInfo,
    DatasetReference,
    FailureInfo,
    GitInfo,
    ModelReference,
    Run,
    RunKind,
    RunRef,
    RunStatus,
)

_STATUS = {
    "notstarted": RunStatus.QUEUED,
    "queued": RunStatus.QUEUED,
    "preparing": RunStatus.QUEUED,
    "provisioning": RunStatus.QUEUED,
    "starting": RunStatus.QUEUED,
    "running": RunStatus.RUNNING,
    "finalizing": RunStatus.RUNNING,
    "canceling": RunStatus.RUNNING,
    "completed": RunStatus.COMPLETED,
    "failed": RunStatus.FAILED,
    "canceled": RunStatus.CANCELED,
    "notresponding": RunStatus.FAILED,
}

_GIT_PROPS = {
    "commit": ("mlflow.source.git.commit", "azureml.git.commit"),
    "branch": ("mlflow.source.git.branch", "azureml.git.branch"),
    "repository": ("mlflow.source.git.repoURL", "azureml.git.repository_uri"),
    "dirty": ("azureml.git.dirty",),
}


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        cur = obj
        for part in n.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
            if cur is None:
                break
        if cur is not None:
            return cur
    return default


def _dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def map_status(raw: str | None) -> RunStatus:
    return _STATUS.get((raw or "").replace(" ", "").lower(), RunStatus.UNKNOWN)


def map_kind(job: Any) -> RunKind:
    t = str(_get(job, "type", default="")).lower()
    tags = {k.lower(): str(v).lower() for k, v in (_get(job, "tags", default={}) or {}).items()}
    if t == "pipeline":
        return RunKind.PIPELINE
    if t == "sweep":
        return RunKind.SWEEP
    if t in {"parallel", "batch"}:
        return RunKind.BATCH
    role = tags.get("role") or tags.get("job_type") or ""
    if "eval" in role:
        return RunKind.EVALUATION
    if "train" in role or t == "command":
        return RunKind.TRAINING
    return RunKind.OTHER


def map_job(job: Any, backend: str, properties: dict[str, str] | None = None) -> Run:
    props = {**(_get(job, "properties", default={}) or {}), **(properties or {})}
    tags = {str(k): str(v) for k, v in (_get(job, "tags", default={}) or {}).items()}
    git = GitInfo(
        **{f: next((props[k] for k in keys if k in props), None) for f, keys in _GIT_PROPS.items()}
        | {"dirty": str(next((props[k] for k in _GIT_PROPS["dirty"] if k in props), "")).lower() == "true" or None}
    )

    inputs = _get(job, "inputs", default={}) or {}
    datasets = []
    for name, inp in inputs.items():
        path = _get(inp, "path")
        if path is not None:
            ver = str(path).rsplit(":", 1)[1] if str(path).count(":") >= 2 else None
            datasets.append(DatasetReference(name=name, uri=str(path), version=ver))
    outputs = _get(job, "outputs", default={}) or {}
    models = [
        ModelReference(name=n, uri=str(_get(o, "path", default="")) or None)
        for n, o in outputs.items()
        if "model" in str(_get(o, "type", default="")).lower()
    ]

    status = map_status(_get(job, "status"))
    failure = None
    if status == RunStatus.FAILED:
        err = _get(job, "error.message", "properties.azureml.runerror", default=None) or props.get(
            "azureml.failureReason"
        )
        failure = FailureInfo(
            message=str(err or "job failed (no error message returned)"), category=_failure_category(str(err or ""))
        )

    creation = _get(job, "creation_context", default=None)
    compute_name = _get(job, "compute")
    resources = _get(job, "resources", default=None)
    params = {str(k): str(v) for k, v in (_get(job, "parameters", default={}) or {}).items()}
    for k, v in inputs.items():
        if not hasattr(v, "path") and not isinstance(v, dict):
            params.setdefault(f"inputs.{k}", str(v))

    parent_id = props.get("azureml.pipelinerunid") or _get(job, "parent_job_name")
    return Run(
        ref=RunRef(backend=backend, run_id=str(_get(job, "name"))),
        name=str(_get(job, "name")),
        display_name=_get(job, "display_name"),
        experiment=_get(job, "experiment_name"),
        kind=map_kind(job),
        status=status,
        created_at=_dt(_get(creation, "created_at")) if creation else None,
        started_at=_dt(props.get("StartTimeUtc") or props.get("mlflow.startTime")),
        ended_at=_dt(props.get("EndTimeUtc") or props.get("mlflow.endTime")),
        user=_get(creation, "created_by") if creation else None,
        description=_get(job, "description"),
        tags=tags,
        params=params,
        command=_get(job, "command"),
        environment=str(_get(job, "environment", default="")) or None,
        git=git,
        compute=ComputeInfo(
            target=str(compute_name) if compute_name else None,
            instance_type=_get(resources, "instance_type") if resources else None,
            node_count=_get(resources, "instance_count") if resources else None,
        ),
        datasets=tuple(datasets),
        models=tuple(models),
        parent=RunRef(backend=backend, run_id=str(parent_id)) if parent_id and parent_id != _get(job, "name") else None,
        failure=failure,
        backend_extras={"studio_url": str(_get(job, "studio_url", "services.Studio.endpoint", default="") or "")},
    )


def _failure_category(msg: str) -> str:
    m = msg.lower()
    if "out of memory" in m or "cuda oom" in m or "outofmemory" in m:
        return "oom"
    if "nan" in m:
        return "nan"
    if "timeout" in m or "timed out" in m:
        return "timeout"
    if "usererror" in m or "traceback" in m:
        return "user_error"
    if "node" in m or "infrastructure" in m or "preempt" in m:
        return "infra"
    return "unknown"
