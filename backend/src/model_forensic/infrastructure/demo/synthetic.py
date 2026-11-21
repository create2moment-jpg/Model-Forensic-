"""Deterministic synthetic runs that tell realistic forensic stories.

The scenario: a camera+LiDAR sparse 3D detector/tracker (baseline, 3 seeds) and a candidate that
adds radar, doubles the temporal window and doubles the LR. The candidate regresses mainly at
long range and in tracking. Additional runs exhibit overfitting, divergence (NaN), loss spikes and
a pipeline with child jobs, so every built-in analysis has something to find.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml

RANGES = ["0-30", "30-60", "60-inf"]
CLASSES = ["car", "pedestrian", "cyclist", "truck"]

BASE_CONFIG: dict[str, Any] = {
    "model": {
        "name": "sparse4d",
        "backbone": "resnet50",
        "num_queries": 900,
        "decoder": {"num_layers": 6, "embed_dims": 256},
        "fusion": {"type": "deformable", "modalities": ["camera", "lidar"]},
    },
    "temporal": {"num_frames": 4, "memory_queue": 600},
    "loss": {"cls_weight": 2.0, "reg_weight": 0.25, "uncertainty": {"enabled": False, "weight": 0.0}},
    "optimizer": {"type": "adamw", "lr": 2.0e-4, "weight_decay": 0.01},
    "schedule": {"warmup_steps": 500, "max_steps": 6000},
    "data": {"dataset": "nuscenes-trainval", "version": "v1.0", "batch_size": 16, "class_balanced_sampling": True},
    "seed": 0,
    "output_dir": "/mnt/out/run",
}


def _deep_set(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def _config(overrides: dict[str, Any]) -> dict[str, Any]:
    cfg = json.loads(json.dumps(BASE_CONFIG))
    for k, v in overrides.items():
        _deep_set(cfg, k, v)
    return cfg


def _loss_curve(
    rng: np.random.Generator, steps: np.ndarray, start: float, end: float, tau: float, noise: float
) -> np.ndarray:
    x = steps / steps.max()
    base = end + (start - end) * np.exp(-x / tau)
    return base * (1 + rng.normal(0, noise, steps.size))


class _Writer:
    def __init__(self, root: Path, run_id: str, secs_per_step: float = 2.5, max_step: float | None = None) -> None:
        self.sps = secs_per_step
        self.max_step = max_step
        self.dir = root / run_id
        (self.dir / "logs").mkdir(parents=True, exist_ok=True)
        (self.dir / "outputs").mkdir(exist_ok=True)
        self.metrics: list[str] = []

    def series(self, key: str, steps: np.ndarray, values: np.ndarray, t0: float) -> None:
        for s, v in zip(steps, values, strict=True):
            val = None if not math.isfinite(float(v)) else round(float(v), 6)
            self.metrics.append(
                json.dumps(
                    {
                        "key": key,
                        "step": int(s),
                        "value": val if val is not None else "NaN",
                        "ts": t0 + float(s) * self.sps,
                    }
                )
            )

    def scalar(self, key: str, step: int, value: float, t0: float) -> None:
        self.metrics.append(
            json.dumps({"key": key, "step": step, "value": round(float(value), 6), "ts": t0 + step * self.sps})
        )

    def finish(self, run: dict[str, Any], config: dict[str, Any], log: str) -> None:
        (self.dir / "run.json").write_text(json.dumps(run, indent=2, default=str))
        lines = self.metrics
        if self.max_step is not None:  # a still-running job has only logged part of its steps
            lines = [m for m in lines if json.loads(m)["step"] <= self.max_step]
        (self.dir / "metrics.jsonl").write_text("\n".join(lines) + "\n")
        (self.dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
        (self.dir / "logs" / "std_log.txt").write_text(log)


def _detector_run(
    root: Path,
    run_id: str,
    *,
    seed: int,
    created: datetime,
    cfg_over: dict[str, Any],
    quality: dict[str, float],
    tags: dict[str, str],
    user: str,
    compute: str,
    commit: str,
    branch: str,
    max_steps: int = 6000,
    overfit: bool = False,
    spikes: int = 0,
    diverge_at: float | None = None,
    description: str = "",
    parent: str | None = None,
    display_name: str | None = None,
    running_frac: float | None = None,
) -> None:
    rng = np.random.default_rng(seed)
    cfg = _config({**cfg_over, "seed": seed, "output_dir": f"/mnt/out/{run_id}"})
    # Wall-clock speed differs per run (longer temporal window, slower GPUs) so "hours since start" is meaningful.
    sps = 2.5 * (1.35 if cfg["temporal"]["num_frames"] > 4 else 1.0) * (1.6 if compute == "v100-pool" else 1.0)
    w = _Writer(root, run_id, secs_per_step=sps, max_step=max_steps * running_frac if running_frac else None)
    t0 = created.timestamp()
    steps = np.arange(50, max_steps + 1, 50)
    lr_peak = float(cfg["optimizer"]["lr"])
    warm = int(cfg["schedule"]["warmup_steps"])
    lr = np.where(
        steps < warm,
        lr_peak * steps / warm,
        lr_peak * 0.5 * (1 + np.cos(np.pi * (steps - warm) / (steps.max() - warm))),
    )
    w.series("train/lr", steps, lr, t0)
    w.series("train/epoch", steps, steps / 500.0, t0)

    lr_scale = lr_peak / 2e-4
    noise = 0.02 * lr_scale
    comps = {
        "loss/cls": _loss_curve(rng, steps, 1.6, 0.42 * quality.get("cls", 1.0), 0.18, noise),
        "loss/bbox": _loss_curve(rng, steps, 1.1, 0.30 * quality.get("bbox", 1.0), 0.25, noise),
        "loss/velocity": np.concatenate(
            [
                _loss_curve(rng, steps[: steps.size // 3], 0.9, 0.55 * quality.get("vel", 1.0), 0.15, noise),
                0.55 * quality.get("vel", 1.0) * (1 + rng.normal(0, noise, steps.size - steps.size // 3)),
            ]
        ),
    }
    if cfg["loss"]["uncertainty"]["enabled"]:
        comps["loss/uncertainty"] = _loss_curve(rng, steps, 0.6, 0.2, 0.3, noise)
    for i in range(spikes):
        idx = int(steps.size * (0.25 + 0.5 * i / max(1, spikes)))
        for c in comps.values():
            c[idx] *= 6.0
    total = sum(comps.values())
    if diverge_at is not None:
        k = int(steps.size * diverge_at)
        growth = np.exp(np.linspace(0, 6, steps.size - k))
        total = total.copy()
        total[k:] = total[k] * growth
        total[k + (steps.size - k) // 2 :] = np.nan
        for c in comps.values():
            c[k:] = c[k] * growth
    for name, c in comps.items():
        w.series(f"train/{name}", steps, c, t0)
    w.series("train/loss", steps, total, t0)
    w.series("train/grad_norm", steps, 3.0 * lr_scale * (1 + rng.normal(0, 0.1, steps.size)), t0)
    w.series(
        "train/positive_query_ratio",
        steps,
        np.clip(0.012 * quality.get("pqr", 1.0) + rng.normal(0, 0.0005, steps.size), 0, 1),
        t0,
    )
    w.series(
        "train/duplicate_rate",
        steps,
        np.clip(0.04 * quality.get("dup", 1.0) + rng.normal(0, 0.003, steps.size), 0, 1),
        t0,
    )

    eval_steps = np.arange(1000, max_steps + 1, 1000)
    if diverge_at is not None:
        eval_steps = eval_steps[eval_steps < steps[int(steps.size * diverge_at)]]
    prog = 1 - np.exp(-eval_steps / (max_steps * 0.35))
    val_loss = 1.9 - 1.0 * prog
    if overfit:
        mid = eval_steps.size // 2
        val_loss[mid:] = val_loss[mid] + np.linspace(0, 0.22, eval_steps.size - mid)
        prog = np.minimum(prog, prog[mid] - np.r_[np.zeros(mid), np.linspace(0, 0.08, eval_steps.size - mid)])
    w.series("val/loss", eval_steps, val_loss * (1 + rng.normal(0, 0.005, eval_steps.size)), t0)

    seed_noise = rng.normal(0, 0.004)
    q = quality
    headline = {
        "NDS": 0.62 * q.get("nds", 1.0) + seed_noise,
        "mAP": 0.55 * q.get("map", 1.0) + seed_noise,
        "AMOTA": 0.48 * q.get("amota", 1.0) + rng.normal(0, 0.005),
        "AMOTP": 1.05 * q.get("amotp", 1.0) + rng.normal(0, 0.01),
        "mATE": 0.52 * q.get("mate", 1.0) + rng.normal(0, 0.004),
        "mAVE": 0.28 * q.get("mave", 1.0) + rng.normal(0, 0.004),
        "IDS": 520 * q.get("ids", 1.0) + rng.normal(0, 8),
        "ECE": 0.06 * q.get("ece", 1.0) + rng.normal(0, 0.002),
        "mean_score": 0.41 * q.get("score", 1.0),
    }
    for name, final in headline.items():
        hib = name not in {"AMOTP", "mATE", "mAVE", "IDS", "ECE"}
        curve = final * (0.55 + 0.45 * prog / prog[-1]) if hib else final * (1.6 - 0.6 * prog / prog[-1])
        if name == "mean_score" and q.get("score", 1.0) < 0.8:
            curve = np.r_[
                curve[: curve.size // 2], np.linspace(curve[curve.size // 2], final, curve.size - curve.size // 2)
            ]
            curve[: curve.size // 2] = 0.41
        w.series(f"val/{name}", eval_steps, curve, t0)
    for r in RANGES:
        f = q.get(f"recall_{r}", 1.0)
        base = {"0-30": 0.78, "30-60": 0.62, "60-inf": 0.41}[r]
        w.scalar(
            f"val/recall[range={r}]", int(eval_steps[-1]) if eval_steps.size else 0, base * f + rng.normal(0, 0.003), t0
        )
        w.scalar(
            f"val/mAP[range={r}]",
            int(eval_steps[-1]) if eval_steps.size else 0,
            {"0-30": 0.68, "30-60": 0.52, "60-inf": 0.31}[r] * f + rng.normal(0, 0.003),
            t0,
        )
    w.scalar(
        "val/recall",
        int(eval_steps[-1]) if eval_steps.size else 0,
        float(np.mean([{"0-30": 0.78, "30-60": 0.62, "60-inf": 0.41}[r] * q.get(f"recall_{r}", 1.0) for r in RANGES]))
        + rng.normal(0, 0.002),
        t0,
    )
    for c in CLASSES:
        w.scalar(
            f"val/AP[class={c}]",
            int(eval_steps[-1]) if eval_steps.size else 0,
            {"car": 0.66, "pedestrian": 0.55, "cyclist": 0.41, "truck": 0.44}[c] * q.get(f"ap_{c}", 1.0)
            + rng.normal(0, 0.004),
            t0,
        )

    failed = diverge_at is not None
    duration = timedelta(seconds=float(steps[-1]) * sps * (0.6 if failed else 1.0) * (running_frac or 1.0))
    log_lines = [f"[{created.isoformat()}] starting {run_id} seed={seed}", f"config: {json.dumps(cfg['model'])}"]
    for s in steps[:: max(1, steps.size // 20)]:
        log_lines.append(f"step {int(s)} loss={float(total[list(steps).index(s)]):.4f}")
    if failed:
        k_div = int(steps.size * (diverge_at or 0))
        nan_step = int(steps[k_div + (steps.size - k_div) // 2])
        log_lines += [
            f"WARNING: loss is NaN at step {nan_step}",
            "Traceback (most recent call last):",
            '  File "train.py", line 211, in <module>',
            "FloatingPointError: Loss is NaN, stopping training",
        ]
    else:
        log_lines.append("training finished")
    (w.dir / "outputs" / "eval_results.json").write_text(
        json.dumps({k: round(v, 5) for k, v in headline.items()}, indent=2)
    )
    (w.dir / "outputs" / "checkpoint_final.pth").write_bytes(rng.bytes(4096))

    run = {
        "name": run_id,
        "display_name": display_name or run_id,
        "experiment": "sparse4d-fusion",
        "kind": "training",
        "status": "failed" if failed else ("running" if running_frac else "completed"),
        "created_at": created.isoformat(),
        "started_at": (created + timedelta(minutes=4)).isoformat(),
        "ended_at": None if running_frac else (created + timedelta(minutes=4) + duration).isoformat(),
        "user": user,
        "description": description,
        "tags": tags,
        "params": {"seed": str(seed)},
        "command": f"python train.py --config configs/{run_id}.yaml",
        "environment": "azureml:sparse4d-env:12",
        "git": {"commit": commit, "branch": branch, "repository": "https://example.invalid/perception/sparse4d"},
        "compute": {
            "target": compute,
            "instance_type": "Standard_ND96asr_v4",
            "gpu_type": "A100-40GB",
            "gpu_count": 8,
            "node_count": 1,
        },
        "datasets": [{"name": "nuscenes", "version": cfg["data"]["version"], "role": "train"}],
        "parent": parent,
        "summary_metrics": {} if running_frac else {k: round(v, 5) for k, v in headline.items()},
    }
    if failed:
        run["failure"] = {
            "message": "FloatingPointError: Loss is NaN, stopping training",
            "category": "nan",
            "log_excerpt": log_lines[-1],
        }
    w.finish(run, cfg, "\n".join(log_lines) + "\n")


def generate_demo(root: Path, now: datetime | None = None) -> list[str]:
    """Write the demo dataset to ``root``. Values are deterministic; dates are relative to ``now``."""
    now = now or datetime.now(timezone.utc)
    root.mkdir(parents=True, exist_ok=True)
    ids: list[str] = []

    def at(days: float) -> datetime:
        return (now - timedelta(days=days)).replace(microsecond=0)

    for i, seed in enumerate([0, 1, 2]):
        rid = f"baseline-cl4f-s{seed}"
        _detector_run(
            root,
            rid,
            seed=seed,
            created=at(12 - i * 0.1),
            cfg_over={},
            quality={},
            tags={"role": "baseline", "modalities": "camera+lidar", "group": "baseline-cl4f"},
            user="alex",
            compute="a100-cluster",
            commit="3f9c2e1",
            branch="main",
            description="Camera+LiDAR baseline, 4-frame temporal window",
        )
        ids.append(rid)

    _detector_run(
        root,
        "cand-radar-8f",
        seed=10,
        created=at(5),
        cfg_over={
            "model.fusion.modalities": ["camera", "lidar", "radar"],
            "temporal.num_frames": 8,
            "temporal.memory_queue": 1200,
            "optimizer.lr": 4.0e-4,
        },
        quality={
            "nds": 0.955,
            "map": 0.965,
            "amota": 0.90,
            "amotp": 1.12,
            "mate": 1.09,
            "mave": 1.05,
            "ids": 1.25,
            "recall_0-30": 1.0,
            "recall_30-60": 0.985,
            "recall_60-inf": 0.84,
            "vel": 1.25,
            "cls": 1.05,
        },
        tags={"role": "candidate", "modalities": "camera+lidar+radar"},
        user="sam",
        compute="a100-cluster",
        commit="a81d07c",
        branch="feat/radar-fusion",
        description="Add radar branch, 8-frame temporal window, 2x LR",
        spikes=0,
    )
    ids.append("cand-radar-8f")

    _detector_run(
        root,
        "cand-uncert",
        seed=11,
        created=at(3),
        cfg_over={"loss.uncertainty.enabled": True, "loss.uncertainty.weight": 0.5},
        quality={"nds": 1.004, "ece": 0.7, "recall_60-inf": 0.95},
        tags={"role": "candidate", "modalities": "camera+lidar", "feature": "uncertainty"},
        user="alex",
        compute="a100-cluster",
        commit="b22e9f0",
        branch="feat/uncertainty",
        description="Aleatoric uncertainty head with NLL loss",
    )
    ids.append("cand-uncert")

    _detector_run(
        root,
        "long-schedule-overfit",
        seed=4,
        created=at(8),
        cfg_over={"schedule.max_steps": 12000},
        quality={"nds": 0.98},
        tags={"role": "experiment"},
        user="sam",
        compute="v100-pool",
        commit="3f9c2e1",
        branch="main",
        max_steps=12000,
        overfit=True,
        description="2x schedule",
    )
    ids.append("long-schedule-overfit")

    _detector_run(
        root,
        "lr-sweep-1e3",
        seed=5,
        created=at(2),
        cfg_over={"optimizer.lr": 1.0e-3},
        quality={},
        tags={"role": "sweep", "sweep": "lr"},
        user="sam",
        compute="v100-pool",
        commit="a81d07c",
        branch="feat/radar-fusion",
        diverge_at=0.55,
        description="LR sweep arm 1e-3",
    )
    ids.append("lr-sweep-1e3")

    _detector_run(
        root,
        "query-1800-spiky",
        seed=6,
        created=at(1),
        cfg_over={"model.num_queries": 1800},
        quality={"pqr": 0.35, "dup": 3.5, "score": 0.6, "nds": 0.99},
        tags={"role": "experiment"},
        user="alex",
        compute="a100-cluster",
        commit="c0ffee1",
        branch="feat/more-queries",
        spikes=3,
        description="Double the number of queries",
    )
    ids.append("query-1800-spiky")

    # Jobs launched by different people from a shared baseline, one still training (exercises live refresh).
    _detector_run(
        root,
        "priya-lr3e4-wd05",
        seed=21,
        created=at(4),
        cfg_over={"optimizer.lr": 3.0e-4, "optimizer.weight_decay": 0.05},
        quality={"nds": 1.006, "cls": 0.97},
        tags={"role": "experiment", "sweep": "wd"},
        user="priya",
        compute="a100-cluster",
        commit="d4e5f61",
        branch="exp/weight-decay",
        description="Higher LR + weight decay",
    )
    ids.append("priya-lr3e4-wd05")
    _detector_run(
        root,
        "priya-bs32",
        seed=22,
        created=at(2.5),
        cfg_over={"data.batch_size": 32, "optimizer.lr": 4.0e-4},
        quality={"nds": 0.995},
        tags={"role": "experiment"},
        user="priya",
        compute="v100-pool",
        commit="d4e5f61",
        branch="exp/weight-decay",
        description="Batch 32, linear LR scaling",
    )
    ids.append("priya-bs32")
    _detector_run(
        root,
        "sam-radar-live",
        seed=23,
        created=at(0.15),
        cfg_over={"model.fusion.modalities": ["camera", "lidar", "radar"]},
        quality={"nds": 0.99},
        tags={"role": "candidate", "modalities": "camera+lidar+radar"},
        user="sam",
        compute="a100-cluster",
        commit="a81d07c",
        branch="feat/radar-fusion",
        description="Radar fusion only (4-frame window) — still training",
        running_frac=0.45,
    )
    ids.append("sam-radar-live")

    # Pipeline with two children (train + eval) to exercise lineage.
    pipe = root / "pipeline-nightly"
    (pipe / "logs").mkdir(parents=True, exist_ok=True)
    (pipe / "run.json").write_text(
        json.dumps(
            {
                "name": "pipeline-nightly",
                "experiment": "nightly",
                "kind": "pipeline",
                "status": "completed",
                "created_at": at(0.5).isoformat(),
                "started_at": at(0.5).isoformat(),
                "ended_at": at(0.2).isoformat(),
                "user": "ci-bot",
                "tags": {"trigger": "schedule"},
                "children": ["nightly-train", "nightly-eval"],
                "git": {"commit": "3f9c2e1", "branch": "main"},
            },
            indent=2,
        )
    )
    (pipe / "logs" / "std_log.txt").write_text("pipeline started\npipeline completed\n")
    _detector_run(
        root,
        "nightly-train",
        seed=7,
        created=at(0.5),
        cfg_over={},
        quality={},
        tags={"role": "nightly"},
        user="ci-bot",
        compute="a100-cluster",
        commit="3f9c2e1",
        branch="main",
        parent="pipeline-nightly",
    )
    ids += ["pipeline-nightly", "nightly-train"]
    ev = root / "nightly-eval"
    (ev / "logs").mkdir(parents=True, exist_ok=True)
    (ev / "run.json").write_text(
        json.dumps(
            {
                "name": "nightly-eval",
                "experiment": "nightly",
                "kind": "evaluation",
                "status": "completed",
                "created_at": at(0.3).isoformat(),
                "user": "ci-bot",
                "parent": "pipeline-nightly",
                "tags": {"role": "eval"},
                "summary_metrics": {"NDS": 0.618, "mAP": 0.549},
            },
            indent=2,
        )
    )
    (ev / "metrics.jsonl").write_text(
        json.dumps({"key": "test/NDS", "step": 0, "value": 0.618})
        + "\n"
        + json.dumps({"key": "test/mAP", "step": 0, "value": 0.549})
        + "\n"
    )
    (ev / "logs" / "std_log.txt").write_text("evaluation done\n")
    ids.append("nightly-eval")
    return ids
