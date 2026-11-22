"""Artifact catalog: triage a run by the question each file family answers — before downloading (v1 ``study_cli
catalog`` / ``inventory``).

Works on the backend's file *listing* (no download): every file is put in a family with what it can tell you, when it
is worth fetching and its interpretation caveat, plus count, size and how much is already cached. A family can then be
downloaded on its own (``/runs/{run}/download`` with explicit paths), so the download is chosen by the question rather
than by bulk.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from model_forensic.application.precision_recall import guide, is_eval_file
from model_forensic.domain.artifacts import Artifact, ArtifactKind

FAMILIES: dict[str, tuple[str, str, str]] = {
    # family: (what it answers, when it is worth fetching, caveat)
    "driver logs": (
        "Why a run failed; which dataset versions were loaded; which GPUs were used; warnings.",
        "Always for failures and for the Dataset / Compute tabs — small.",
        "Multi-node runs have one log per node; the largest file is usually the driver log.",
    ),
    "code diff": (
        "Which code the job actually ran: uncommitted changes against its commit (the job's own diff).",
        "Always when comparing runs whose configs look alike — small; also shown on the Timeline tab.",
        "Raw evidence: a diff says what changed, not whether the change matters.",
    ),
    "composed configs": (
        "The exact resolved configuration: datasets, LR schedule, loss weights, rulers, checkpoint monitor.",
        "Always — small, and needed for config diffs and the pitfall checks.",
        "Python-tagged YAML is read as data; per-task blocks can override top-level values.",
    ),
    "training curves (metrics.csv)": (
        "Per-step losses and the LR schedule as Lightning logged them.",
        "When run-history metrics are sparse or the LR schedule is needed.",
        "Steps are Lightning global steps; compare runs on the same axis.",
    ),
    "checkpoints": (
        "The model weights (resume, re-evaluate, inspect).",
        "Only to re-evaluate or debug a specific checkpoint — large.",
        "'best' is only meaningful if the checkpoint monitor metric is actually logged (P4).",
    ),
    "metric histories": (
        "Every logged metric series (the Metrics tab reads them through the metrics API already).",
        "Only for offline analysis or an export.",
        "Sampled histories; the run-history API may down-sample long series.",
    ),
    "run metadata": (
        "Job metadata: status, user, compute, tags, timings.",
        "Rarely — the index already holds it.",
        "Backend-specific fields; the run header shows the normalised view.",
    ),
    "environment": (
        "Conda / pip / Docker environment the job ran in.",
        "When two runs differ unexpectedly and the code is identical.",
        "Image tags can float; prefer digests when comparing.",
    ),
}
ORDER = [
    "driver logs",
    "code diff",
    "composed configs",
    "training curves (metrics.csv)",
    "working point",
    "pr curve",
    "distance-stratified PR",
    "false-positive analysis",
    "attribute confusion matrix",
    "3D match evaluation",
    "per-sequence table",
    "scalar metrics",
    "evaluation plots",
    "other evaluation output",
    "checkpoints",
    "metric histories",
    "run metadata",
    "environment",
    "other files",
]


def family_of(a: Artifact) -> tuple[str, str, str, str]:
    """(family, answers, worth fetching, caveat)."""
    p = a.path.casefold()
    name = p.rsplit("/", 1)[-1]
    if name == "metrics.csv":
        return ("training curves (metrics.csv)", *FAMILIES["training curves (metrics.csv)"])
    if is_eval_file(a.path):
        g = guide(a.path)
        if g["family"] != "other evaluation output" or "evaluation/" in p:
            return g["family"], g["purpose"], "For the Precision & recall tab and the study book.", g["caveat"]
    if "repo_diff" in name or name.endswith((".diff", ".patch")):
        return ("code diff", *FAMILIES["code diff"])
    if a.kind == ArtifactKind.LOG or (name.endswith((".log", ".txt")) and "log" in p):
        return ("driver logs", *FAMILIES["driver logs"])
    if a.kind == ArtifactKind.CONFIG or name.endswith(("config.yaml", "config.json")):
        return ("composed configs", *FAMILIES["composed configs"])
    if a.kind in (ArtifactKind.CHECKPOINT, ArtifactKind.MODEL) or name.endswith(
        (".ckpt", ".pt", ".pth", ".safetensors")
    ):
        return ("checkpoints", *FAMILIES["checkpoints"])
    if a.kind == ArtifactKind.METRICS or name in {"metrics.jsonl", "metric_histories.json"}:
        return ("metric histories", *FAMILIES["metric histories"])
    if name in {"run.json", "pipeline_info.json"}:
        return ("run metadata", *FAMILIES["run metadata"])
    if a.kind == ArtifactKind.ENVIRONMENT:
        return ("environment", *FAMILIES["environment"])
    return "other files", "Files the catalog does not recognise.", "Only if you know you need them.", "Not interpreted."


def catalog(svc: Any, run: str) -> dict[str, Any]:
    ref = svc.ref(run)
    arts = svc.artifacts(ref)
    fams: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for a in arts:
        fam, answers, worth, caveat = family_of(a)
        f = fams.setdefault(
            fam,
            {
                "family": fam,
                "answers": answers,
                "worth": worth,
                "caveat": caveat,
                "files": 0,
                "bytes": 0,
                "unsized": 0,
                "cached": 0,
                "stages": set(),
                "classes": set(),
                "paths": [],
            },
        )
        f["files"] += 1
        if a.size_bytes is None:
            f["unsized"] += 1
        else:
            f["bytes"] += a.size_bytes
        if svc.artifact_store.cached(a) is not None:
            f["cached"] += 1
        if a.stage:
            f["stages"].add(a.stage)
        cls = guide(a.path)["class"]
        if cls:
            f["classes"].add(cls)
        f["paths"].append(f"{a.run}::{a.path}" if a.stage else a.path)
    rows = sorted(fams.values(), key=lambda f: ORDER.index(f["family"]) if f["family"] in ORDER else 99)
    for f in rows:
        f["stages"], f["classes"] = sorted(f["stages"]), sorted(f["classes"])
    return {"families": rows, "files": len(arts), "bytes": sum(a.size_bytes or 0 for a in arts)}
