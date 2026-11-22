"""Per-run information pages: dataset provenance and compute details, per pipeline stage.

Configs come from ``ForensicService.configs`` (pipeline stage configs are prefixed ``<stage>/``), log lines from
``run_logs.scan_logs`` over every log of the run and its stages. Results are cached per run for finished runs.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from model_forensic.analysis.provenance import LOG_PATTERNS, LogLine, compute_details, dataset_provenance
from model_forensic.application.run_logs import scan_logs

_CACHE: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()


def _stage_of(source: str, stages: list[str]) -> str:
    head = source.split("/", 1)[0]
    return head if "/" in source and head in stages else ""


def _gather(svc: Any, ref: str) -> tuple[list[str], list[tuple[str, str, dict[str, Any]]], list[LogLine], Any]:
    r = svc.ref(ref)
    stages = [name for name, _ in svc.stages(r)]
    cfgs = [
        (_stage_of(c.source, stages), c.source, dict(c.data))
        for c in svc.configs(r)
        if c.source != "params" and isinstance(c.data, dict)
    ]
    scan = scan_logs(svc, r, LOG_PATTERNS)
    lines = [LogLine(h.stage, h.path, h.line) for h in scan.hits]
    return stages, cfgs, lines, scan


def _cached(svc: Any, ref: str, kind: str, build: Any) -> dict[str, Any]:
    run = svc.get_run(ref)
    key = (str(run.ref), kind)
    if run.status.terminal and key in _CACHE:
        return _CACHE[key]
    out: dict[str, Any] = build()
    if run.status.terminal:
        _CACHE[key] = out
        while len(_CACHE) > 128:
            _CACHE.popitem(last=False)
    return out


def run_dataset(svc: Any, ref: str, refresh: bool = False) -> dict[str, Any]:
    """Requested vs loaded dataset per stage (pretrain and finetune often use different data)."""
    if refresh:
        _CACHE.pop((str(svc.ref(ref)), "dataset"), None)

    def build() -> dict[str, Any]:
        stages, cfgs, lines, scan = _gather(svc, ref)
        groups = ["", *stages] if stages else [""]
        out = []
        for st in groups:
            c = [(src, d) for s, src, d in cfgs if s == st]
            ls = [ln for ln in lines if ln.stage == st]
            if not c and not ls:
                continue
            p = dataset_provenance(c, ls)
            if p.configured or p.loaded or p.dataset_name or p.catalog_basepath:
                out.append({"stage": st, **p.to_dict()})
        notes = list(scan.notes)
        if not out:
            notes.append(
                "no dataset information found — it comes from the composed config (Configs preset) and the driver "
                "log (Logs preset)."
            )
        distinct = {(x["dataset_name"], tuple((f["split"], f["version"]) for f in x["configured"])) for x in out}
        if len(out) > 1 and len(distinct) > 1:
            notes.insert(0, "the stages use different datasets or versions — compare them before comparing metrics.")
        return {"stages": out, "notes": notes, "log_files": scan.files}

    return _cached(svc, ref, "dataset", build)


def run_compute(svc: Any, ref: str, refresh: bool = False) -> dict[str, Any]:
    """GPUs actually used (logs) and requested parallelism (config), per stage, plus the job's compute target."""
    if refresh:
        _CACHE.pop((str(svc.ref(ref)), "compute"), None)

    def build() -> dict[str, Any]:
        run = svc.get_run(ref)
        stages, cfgs, lines, scan = _gather(svc, ref)
        platform = {
            "compute_target": run.compute.target,
            "instance_type": run.compute.instance_type,
            "gpu_type": run.compute.gpu_type,
            "gpu_count": run.compute.gpu_count,
            "node_count": run.compute.node_count,
        }
        stage_refs = dict(svc.stages(run.ref))

        def platform_of(st: str) -> dict[str, Any]:
            if not st:
                return platform
            try:
                cr = svc.get_run(stage_refs[st]).compute
            except Exception:  # stage metadata unavailable: the table still shows log/config facts
                return {}
            return {
                "compute_target": cr.target,
                "instance_type": cr.instance_type,
                "gpu_type": cr.gpu_type,
                "gpu_count": cr.gpu_count,
                "node_count": cr.node_count,
            }

        groups = ["", *stages] if stages else [""]
        out = []
        for st in groups:
            c = [(src, d) for s, src, d in cfgs if s == st]
            ls = [ln for ln in lines if ln.stage == st]
            if not c and not ls and st:
                continue
            d = compute_details(c, ls, platform_of(st))
            if d.gpus or d.parallelism or d.cpu_only or d.platform:
                out.append({"stage": st, **d.to_dict()})
        return {"stages": out, "notes": scan.notes, "log_files": scan.files}

    return _cached(svc, ref, "compute", build)
