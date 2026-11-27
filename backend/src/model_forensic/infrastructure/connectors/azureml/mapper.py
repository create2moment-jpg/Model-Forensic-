"""Pure mapping from Azure ML SDK v2 entities to the domain model.

Kept free of SDK imports (duck-typed via ``getattr``) so it is unit-testable with plain
objects and survives minor SDK attribute changes. This is the ONLY place that knows Azure's
shape of a job.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from model_forensic.domain.run import (
    ComputeInfo,
    DatasetReference,
    FailureInfo,
    GitInfo,
    JobIO,
    ModelReference,
    PipelineEdge,
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


def _is_raw_arm(job: Any) -> bool:
    """Raw ARM ``JobBase`` records wrap everything in a ``properties`` envelope (entity jobs have a dict there)."""
    env = getattr(job, "properties", None)
    return env is not None and not isinstance(env, dict) and hasattr(env, "display_name")


def unwrap_raw_job(job: Any) -> Any:
    """View a raw ARM record with the entity attribute names the mapper expects."""
    from types import SimpleNamespace

    env = job.properties
    sd = getattr(job, "system_data", None)
    services = getattr(env, "services", None) or {}
    studio = services.get("Studio") if isinstance(services, dict) else None
    return SimpleNamespace(
        name=job.name,
        display_name=getattr(env, "display_name", None),
        experiment_name=getattr(env, "experiment_name", None),
        type=str(getattr(getattr(env, "job_type", None), "value", getattr(env, "job_type", "")) or "").lower(),
        status=str(getattr(getattr(env, "status", None), "value", getattr(env, "status", "")) or ""),
        tags=getattr(env, "tags", None) or {},
        properties=getattr(env, "properties", None) or {},
        description=getattr(env, "description", None),
        inputs=getattr(env, "inputs", None) or {},
        outputs=getattr(env, "outputs", None) or {},
        jobs=getattr(env, "jobs", None) or {},
        objective=getattr(env, "objective", None),
        compute=str(getattr(env, "compute_id", "") or "").rsplit("/", 1)[-1] or None,
        resources=getattr(env, "resources", None),
        parameters={},
        command=getattr(env, "command", None),
        environment=getattr(env, "environment_id", None),
        parent_job_name=getattr(env, "parent_job_name", None),
        studio_url=getattr(studio, "endpoint", None) if studio is not None else None,
        creation_context=SimpleNamespace(
            created_at=getattr(sd, "created_at", None), created_by=getattr(sd, "created_by", None)
        )
        if sd is not None
        else None,
    )


_BINDING = re.compile(r"\$\{\{\s*parent\.jobs\.([\w.-]+)\.outputs\.([\w.-]+)\s*\}\}")


def pipeline_edges(jobs: Any) -> list[PipelineEdge]:
    """A pipeline's graph from its definition: each step input bound to ``${{parent.jobs.<step>.outputs.<out>}}``
    is an edge from that step. Works on the raw ARM JSON (dicts) and on SDK entities (``Input(path=…)``)."""
    out: list[PipelineEdge] = []
    for target, node in (jobs or {}).items():
        inputs = _get(node, "inputs", default={}) or {}
        for name, inp in inputs.items() if isinstance(inputs, dict) else []:
            ref = inp if isinstance(inp, str) else _get(inp, "value", "path", "uri")
            for m in _BINDING.finditer(str(ref or "")):
                out.append(PipelineEdge(source=m.group(1), output=m.group(2), target=str(target), input=str(name)))
    return out


_SECRET = re.compile(r"secret|password|passwd|token|sas|credential|connection.?string|account.?key|api.?key", re.I)
_ASSET = re.compile(
    r"^azureml:(?://)?(?:.*/)?(?:data|models)/(?P<n1>[^/]+)/versions/(?P<v1>[^/]+)$|^azureml:(?P<n2>[^:/]+):(?P<v2>[^:/]+)$"
)


def redact(props: dict[str, Any]) -> dict[str, str]:
    """A job's properties for display: values of secret-looking keys (token, SAS, password, key …) hidden."""
    return {str(k): ("•••• (hidden)" if _SECRET.search(str(k)) else str(v)) for k, v in props.items()}


def _io(name: str, item: Any, direction: str) -> JobIO:
    path = _get(item, "path", "uri")
    typ = _get(item, "type", "job_input_type", "job_output_type")
    typ = str(getattr(typ, "value", typ)) if typ is not None else None
    mode = _get(item, "mode")
    mode = str(getattr(mode, "value", mode)) if mode is not None else None
    if path is None and not isinstance(item, (dict,)) and not hasattr(item, "__dict__"):
        return JobIO(name=name, direction=direction, type="literal", value=str(item))
    if path is None and _get(item, "value") is not None:
        return JobIO(name=name, direction=direction, type="literal", value=str(_get(item, "value")))
    asset = version = None
    m = _ASSET.match(str(path or ""))
    if m:
        asset, version = m.group("n1") or m.group("n2"), m.group("v1") or m.group("v2")
    return JobIO(name=name, direction=direction, type=typ, asset=asset, version=version,
                 path=str(path) if path else None, mode=mode)  # fmt: skip


def map_job(job: Any, backend: str, properties: dict[str, str] | None = None) -> Run:
    if _is_raw_arm(job):
        job = unwrap_raw_job(job)
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
    io = [_io(str(n), v, "input") for n, v in inputs.items()] + [_io(str(n), v, "output") for n, v in outputs.items()]
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
    params.update(objective(job))
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
        properties=redact(props),
        edges=tuple(pipeline_edges(_get(job, "jobs", default={}))),
        io=tuple(io),
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


def trial_params(meta: dict[str, str]) -> dict[str, str]:
    """A sweep trial's hyper-parameters from its run record: the ``hyperparameters`` JSON (HyperDrive / sweep) — keys
    without the ``--`` of a command-line argument."""
    import json

    for key in ("hyperparameters", "_aml_system_hyperparameters", "azureml.hyperparameters"):
        raw = meta.get(key)
        if raw:
            try:
                got = json.loads(raw)
            except ValueError:
                continue
            if isinstance(got, dict):
                return {str(k).lstrip("-"): str(v) for k, v in got.items()}
    return {}


def objective(job: Any) -> dict[str, str]:
    """A sweep's objective as params (``objective.primary_metric``, ``objective.goal``) — empty for other jobs."""
    obj = _get(job, "objective", default=None)
    pm, goal = _get(obj, "primary_metric"), _get(obj, "goal")
    out = {}
    if pm:
        out["objective.primary_metric"] = str(pm)
    if goal:
        out["objective.goal"] = str(getattr(goal, "value", goal)).lower()
    return out


def map_child(child: Any, backend: str, parent: Run, stage: str) -> Run:
    """A pipeline stage as a run of its own, from the run-history child listing (``runId``, ``status``, start / end,
    ``properties``, ``tags``, ``error``) and its pipeline (experiment, owner, code version, compute). The stage runs
    are not in the job listing: without this they had no name, status or parent anywhere."""
    run_id = str(_get(child, "runId", "run_id", default=""))
    tags = {str(k): str(v) for k, v in (_get(child, "tags", default={}) or {}).items()}
    low = stage.lower()
    kind = RunKind.EVALUATION if "eval" in low else RunKind.TRAINING if "train" in low else RunKind.OTHER
    status = map_status(_get(child, "status"))
    err = _get(child, "error", default=None) or {}
    msg = ""
    if isinstance(err, dict):
        inner = err.get("error") if isinstance(err.get("error"), dict) else err
        msg = str(inner.get("message") or "") if isinstance(inner, dict) else ""
    failure = (
        FailureInfo(message=msg[:2000], category=_failure_category(msg)) if status == RunStatus.FAILED and msg else None
    )
    # a sweep trial: its hyper-parameters (the run-history record carries them as JSON in a tag or property)
    props = {str(k): str(v) for k, v in (_get(child, "properties", default={}) or {}).items()}
    params = trial_params({**props, **tags}) if parent.kind == RunKind.SWEEP else {}
    if parent.kind == RunKind.SWEEP:
        kind = RunKind.TRAINING
    return Run(
        ref=RunRef(backend=backend, run_id=run_id),
        name=stage,
        display_name=stage,
        params=params,
        experiment=parent.experiment,
        kind=kind,
        status=status,
        created_at=_dt(_get(child, "createdUtc", "created_utc")) or _dt(_get(child, "startTimeUtc")),
        started_at=_dt(_get(child, "startTimeUtc", "start_time_utc")),
        ended_at=_dt(_get(child, "endTimeUtc", "end_time_utc")),
        user=parent.user,
        description=f"{'trial' if parent.kind == RunKind.SWEEP else 'stage'} {stage} of {parent.display_name or parent.name}",
        tags=tags,
        git=parent.git,
        compute=parent.compute,
        parent=parent.ref,
        failure=failure,
        backend_extras={"stage": stage, "pipeline": parent.ref.run_id},
    )


def with_history_times(run: Run, rec: Any) -> Run:
    """``run`` with the created / start / end time from its run-history record where the job resource had none (what
    studio shows); times the job already carries are kept. Marks where the times came from."""
    upd: dict[str, Any] = {}
    for field, names in (("created_at", ("createdUtc", "created_utc")), ("started_at", ("startTimeUtc", "start_time_utc")),
                         ("ended_at", ("endTimeUtc", "end_time_utc"))):  # fmt: skip
        if getattr(run, field) is None:
            v = _dt(_get(rec, *names))
            if v is not None:
                upd[field] = v
    if not upd:
        return run
    return run.model_copy(update={**upd, "backend_extras": {**run.backend_extras, "times_from": "run history"}})
