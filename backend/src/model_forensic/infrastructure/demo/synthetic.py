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
    kit: dict[str, Any] | None = None,
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

    if kit is not None:
        _kit_metrics(w, steps, eval_steps, lr, lr_peak, t0, seed, cfg, headline, comps, kit, running=bool(running_frac))
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


def _kit_metrics(
    w: _Writer,
    steps: np.ndarray,
    eval_steps: np.ndarray,
    lr: np.ndarray,
    lr_peak: float,
    t0: float,
    seed: int,
    cfg: dict[str, Any],
    headline: dict[str, float],
    comps: dict[str, np.ndarray],
    kit: dict[str, Any],
    running: bool,
) -> None:
    """Metrics the instrumentation kit would log: module health, feature stats, decoder layers, ablations.

    Uses its own RNG so the classic demo metrics stay identical.
    """
    rng = np.random.default_rng(seed + 1000)
    mods = [m for m in ("camera", "lidar", "radar") if m in cfg["model"]["fusion"]["modalities"]] + [
        "fusion",
        "decoder",
    ]
    ks = steps[::4]
    lr_k = lr[::4] / lr_peak
    grad_scale = {"camera": 1.2, "lidar": 0.9, "radar": 0.45, "fusion": 0.6, "decoder": 1.5}
    for m in mods:
        mult = kit.get("radar_lr_mult", 1.0) if m == "radar" else 1.0
        upd = (2.5e-3 * lr_k + 2e-4) * mult * np.exp(rng.normal(0, 0.12, ks.size))
        w.series(f"train/update_ratio[module={m}]", ks, upd, t0)
        w.series(
            f"train/grad_norm[module={m}]",
            ks,
            grad_scale[m] * (0.6 + 0.4 * np.exp(-ks / ks.max() * 3)) * np.exp(rng.normal(0, 0.1, ks.size)),
            t0,
        )
        w.series(f"train/grad_dead_frac[module={m}]", ks, np.zeros(ks.size), t0)
    fs = steps[::8]
    prog = fs / fs.max()
    for i, base in enumerate((1.0, 0.8, 0.6, 0.5)):
        w.series(
            f"train/feat_rms[module=camera_fpn,scale={i}]",
            fs,
            base * (1 + 0.1 * prog) * np.exp(rng.normal(0, 0.03, fs.size)),
            t0,
        )
        w.series(
            f"train/feat_std[module=camera_fpn,scale={i}]", fs, 0.9 * base * np.exp(rng.normal(0, 0.03, fs.size)), t0
        )
        w.series(
            f"train/feat_zero_frac[module=camera_fpn,scale={i}]",
            fs,
            np.clip(0.35 + rng.normal(0, 0.01, fs.size), 0, 1),
            t0,
        )
    if "radar" in mods:
        levels = int(cfg["model"].get("radar_encoder", {}).get("fpn_levels", 1))
        for i in range(levels):
            base = (0.6, 0.5, 0.4)[i]
            rms = base * np.exp(rng.normal(0, 0.04, fs.size))
            zero = np.clip(0.45 + 0.05 * i + rng.normal(0, 0.01, fs.size), 0, 1)
            std = 0.8 * rms
            if kit.get("radar_scale_collapse") and i == levels - 1:
                dying = prog > 0.3
                rms = np.where(dying, base * np.exp(-(prog - 0.3) * 40) + 1e-5, rms)
                zero = np.where(dying, np.minimum(0.99, zero + (prog - 0.3) * 3), zero)
                std = np.where(dying, rms * 0.5, std)
            w.series(f"train/feat_rms[module=radar_fpn,scale={i}]", fs, rms, t0)
            w.series(f"train/feat_std[module=radar_fpn,scale={i}]", fs, std, t0)
            w.series(f"train/feat_zero_frac[module=radar_fpn,scale={i}]", fs, zero, t0)
    for term in ("cls", "bbox"):
        comp = comps[f"loss/{term}"][::4]
        for k in range(6):
            factor = 1 + 0.55 * np.exp(-k / 0.7) - 0.55 * np.exp(-5 / 0.7)
            w.series(f"train/loss/{term}[layer={k}]", ks, comp * factor * np.exp(rng.normal(0, 0.003, ks.size)), t0)
    if kit.get("stream"):
        _stream_metrics(w, ks, comps, t0, rng, kit["stream"])
    if running or not eval_steps.size:
        return
    step = int(eval_steps[-1])
    if kit.get("stream"):
        _stream_eval(w, step, headline, t0, rng, kit["stream"])
    contrib = {"camera": (0.24, 0.12), "lidar": (0.42, 0.55), "radar": kit.get("radar_contrib", (0.02, 0.08))}
    recall = {"0-30": 0.78, "30-60": 0.62, "60-inf": 0.41}
    for ab in ["none", *(f"no_{m}" for m in mods if m in contrib)]:
        overall, far = contrib[ab[3:]] if ab != "none" else (0.0, 0.0)
        for name in ("NDS", "mAP"):
            w.scalar(f"val/{name}[ablation={ab}]", step, headline[name] * (1 - overall) + rng.normal(0, 0.001), t0)
        for r, base in recall.items():
            drop = {"0-30": overall * 0.6, "30-60": (overall + far) / 2, "60-inf": far}[r]
            w.scalar(
                f"val/recall[ablation={ab},range={r}]",
                step,
                base * kit.get("recall_scale", {}).get(r, 1.0) * (1 - drop),
                t0,
            )


def _stream_metrics(
    w: _Writer, ks: np.ndarray, comps: dict[str, np.ndarray], t0: float, rng: np.random.Generator, st: dict[str, Any]
) -> None:
    """StreamPETR-style training diagnostics: DN / aux-2D branches, sequence position, query sources,
    uncertainty statistics and the loss-gradient probe. ``st`` selects the failure modes."""
    prog = ks / ks.max()
    noise = lambda sd=0.01: np.exp(rng.normal(0, sd, ks.size))  # noqa: E731
    bbox = comps["loss/bbox"][::4]
    cls = comps["loss/cls"][::4]
    dn_ratio = st.get("dn_ratio", 0.45)
    for k in range(6):
        f = 1 + 0.4 * np.exp(-k / 0.8)
        w.series(f"train/loss/bbox[branch=dn,layer={k}]", ks, bbox * f * dn_ratio * noise(), t0)
        w.series(f"train/loss/cls[branch=dn,layer={k}]", ks, cls * f * dn_ratio * noise(), t0)
    w.series("train/loss/bbox[branch=aux2d]", ks, 0.6 * bbox * noise(), t0)
    w.series("train/loss/centerness[branch=aux2d]", ks, 0.4 + 0.2 * np.exp(-prog * 4) * noise(), t0)
    mem = st.get("memory_gain", 0.12)
    for pos, g in (("0", 0.0), ("1-3", mem * 0.6), ("4-inf", mem)):
        w.series(f"train/loss/bbox[seqpos={pos}]", ks, bbox * (1.06 - g) * noise(), t0)
        w.series(f"train/loss/cls[seqpos={pos}]", ks, cls * (1.04 - g / 2) * noise(), t0)
    w.series(
        "train/q_from_memory_frac",
        ks,
        (st.get("memory_share", 0.42) * (0.3 + 0.7 * np.minimum(1, prog * 3))) * noise(0.04),
        t0,
    )
    w.series("train/q_pos_frac[source=propagated]", ks, st.get("prop_pos", 0.18) * noise(0.04), t0)
    w.series("train/q_pos_frac[source=new]", ks, 0.06 * noise(0.04), t0)
    for dim, err0 in (("x", 0.35), ("y", 0.35), ("w", 0.12), ("vel", 0.55)):
        err = err0 * (1 - 0.45 * (1 - np.exp(-prog * 3)))
        scale = err * 0.95
        if dim == "vel" and st.get("attenuation"):
            err = err0 * (1 - 0.05 * prog)  # velocity error stays flat
            scale = err0 * (0.8 + 1.4 * prog)  # while predicted scale is inflated
        w.series(f"train/unc_abs_err[dim={dim}]", ks, err * noise(0.03), t0)
        w.series(f"train/unc_scale[dim={dim}]", ks, scale * noise(0.03), t0)
        w.series(f"train/unc_nerr[dim={dim}]", ks, (err / scale) * noise(0.03), t0)
    terms = {"cls": 0.9, "bbox": 1.0, "unc": st.get("unc_grad", 0.6), "dn": 0.5, "aux2d": 0.4}
    for t, g in terms.items():
        w.series(f"train/term_grad_norm[term={t}]", ks, g * (0.6 + 0.4 * np.exp(-prog * 3)) * noise(0.08), t0)
    conflict = st.get("unc_bbox_cos", 0.15)
    pairs = {("bbox", "cls"): 0.12, ("bbox", "dn"): 0.55, ("bbox", "unc"): conflict, ("cls", "aux2d"): 0.08}
    for (a, b), c in pairs.items():
        cos = c * np.minimum(1, 0.4 + prog) + rng.normal(0, 0.04, ks.size)
        w.series(f"train/term_grad_cos[a={a},b={b}]", ks, np.clip(cos, -1, 1), t0)


def _stream_eval(
    w: _Writer, step: int, headline: dict[str, float], t0: float, rng: np.random.Generator, st: dict[str, Any]
) -> None:
    mem = st.get("memory_gain", 0.12)
    for pos, g in (("0", 0.0), ("1-3", mem * 0.6), ("4-inf", mem)):
        w.scalar(f"val/NDS[seqpos={pos}]", step, headline["NDS"] * (0.94 + g / 2) + rng.normal(0, 0.001), t0)
    far_over = st.get("far_overconfidence", 1.1)
    for dim, base in (("x", 1.02), ("y", 1.0), ("w", 0.97), ("vel", 1.9 if st.get("attenuation") else 1.05)):
        corr = 0.05 if (dim == "vel" and st.get("attenuation")) else 0.45
        base *= 0.7 if dim == "vel" and st.get("attenuation") else 1.0
        w.scalar(f"val/unc_nerr[dim={dim}]", step, base, t0)
        w.scalar(f"val/unc_corr[dim={dim}]", step, corr + rng.normal(0, 0.01), t0)
        w.scalar(f"val/unc_cov[dim={dim},level=68]", step, 0.68 / max(base, 0.5) ** 0.5, t0)
        w.scalar(f"val/unc_clamped_frac[dim={dim}]", step, 0.03, t0)
        for r, f in (("0-30", 1.0), ("30-60", (1 + far_over) / 2), ("60-inf", far_over)):
            w.scalar(f"val/unc_nerr[dim={dim},range={r}]", step, base * f, t0)


def _xflow_stage_files(
    d: Path,
    rng: np.random.Generator,
    cfg: dict[str, Any],
    *,
    train: bool,
    evaluate: bool,
    steps: np.ndarray,
    total_w: np.ndarray,
    quality: float,
    class_q: dict[str, float] | None,
) -> None:
    """Files in the layout xflow pipelines write to their stage outputs (listed as ``stage_output/…``)."""
    so = d / "stage_output"
    (d / "user_logs").mkdir(parents=True, exist_ok=True)
    (d / "user_logs" / "std_log.txt").write_text(
        (d / "logs" / "std_log.txt").read_text() if (d / "logs" / "std_log.txt").exists() else ""
    )
    if train:
        summ = so / "summaries" / "train"
        summ.mkdir(parents=True, exist_ok=True)
        (summ / "composed_config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
        lines = ["step,train_weighted_total_loss,learning_rate"] + [
            f"{int(st)},{total_w[i]:.5f},{2e-4 * 0.5 * (1 + math.cos(math.pi * st / steps.max())):.3e}"
            for i, st in enumerate(steps)
        ]
        (summ / "metrics.csv").write_text("\n".join(lines) + "\n")
        ck = so / "checkpoints"
        ck.mkdir(parents=True, exist_ok=True)
        (ck / "last.ckpt").write_bytes(rng.bytes(8192))
        (ck / f"epoch=9-step={int(steps[-1])}.ckpt").write_bytes(rng.bytes(8192))
    if evaluate:
        ev = so / "evaluation" / "offline" / "predict" / "iteration_00000000" / "sparse_detection" / "evaluation"
        ev.mkdir(parents=True, exist_ok=True)
        _write_eval_parquets(ev, rng, quality, class_q or {})
        summ = so / "evaluation" / "offline" / "sparse_detection"
        summ.mkdir(parents=True, exist_ok=True)
        (summ / "summary.json").write_text(json.dumps({"mean_f1_score": round(0.5 * quality, 4)}, indent=2))
        (so / "pipeline_info.json").write_text(json.dumps({"stage": cfg.get("stage", "evaluate")}, indent=2))


_CLASSES_XF = {"PassengerCar": 0.71, "Pedestrian": 0.52, "Cyclist": 0.41, "Truck": 0.48, "Animal": 0.12}


def _write_eval_parquets(out: Path, rng: np.random.Generator, quality: float, class_q: dict[str, float]) -> None:
    """Per-sequence detection / tracking / combined tables with the real index (sequence_hash, class_name).

    Written only when pyarrow is installed (``model-forensic[eval]``)."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return
    seqs = [f"{h:016x}" for h in np.random.default_rng(7).integers(0, 2**63, 24)]  # same evaluation set in every run
    hard = set(seqs[:3])  # e.g. night / rain sequences
    rows: dict[str, list[Any]] = {
        k: []
        for k in (
            "sequence_hash",
            "class_name",
            "AP",
            "AP_per_distance_0_5",
            "AP_per_distance_1_0",
            "AP_per_distance_2_0",
            "AP_per_distance_4_0",
            "ATE",
            "ASE",
            "AOE",
            "AVE",
            "AAE",
            "num_gt",
            "num_pred",
            "num_tp",
            "num_fp",
            "num_fn",
            "num_frames",
        )
    }
    trk: dict[str, list[Any]] = {
        k: [] for k in ("sequence_hash", "class_name", "assa_alpha", "idf1", "mota", "num_frames")
    }
    for sq in seqs:
        frames = int(rng.integers(150, 400))
        for c, base in _CLASSES_XF.items():
            ap = float(
                np.clip(
                    base * quality * class_q.get(c, 1.0) * (0.45 if sq in hard else 1.0) + rng.normal(0, 0.05), 0, 1
                )
            )
            gt = int(rng.integers(20, 300)) if c != "Animal" else int(rng.integers(0, 8))
            tp = int(gt * np.clip(ap + 0.15, 0, 1))
            fp_rate = 1.6 if c == "Pedestrian" and class_q.get(c, 1.0) < 1 else (0.9 if sq in hard else 0.25)
            fp = int(gt * fp_rate * rng.uniform(0.6, 1.4))
            for k, v in (
                ("sequence_hash", sq),
                ("class_name", c),
                ("AP", ap),
                ("AP_per_distance_0_5", ap * 0.7),
                ("AP_per_distance_1_0", ap * 0.85),
                ("AP_per_distance_2_0", ap),
                ("AP_per_distance_4_0", min(1.0, ap * 1.1)),
                ("ATE", 0.3 / max(ap, 0.05) * 0.2),
                ("ASE", 0.15),
                ("AOE", 0.2),
                ("AVE", 0.5),
                ("AAE", 0.12),
                ("num_gt", gt),
                ("num_pred", tp + fp),
                ("num_tp", tp),
                ("num_fp", fp),
                ("num_fn", gt - tp),
                ("num_frames", frames),
            ):
                rows[k].append(v)
            for k, v in (
                ("sequence_hash", sq),
                ("class_name", c),
                ("assa_alpha", ap * 0.9),
                ("idf1", ap * 0.85),
                ("mota", ap * 0.7),
                ("num_frames", frames),
            ):
                trk[k].append(v)
    meta = {
        b"pandas": json.dumps(
            {
                "index_columns": ["sequence_hash", "class_name"],
                "column_indexes": [],
                "columns": [],
                "pandas_version": "2.2.0",
            }
        ).encode()
    }
    for name, cols in (
        ("per_sequence_detection_metrics.parquet", rows),
        ("per_sequence_tracking_metrics.parquet", trk),
    ):
        table = pa.table(cols).replace_schema_metadata(meta)
        pq.write_table(table, out / name)


def _xflow_run(
    root: Path,
    run_id: str,
    *,
    seed: int,
    created: datetime,
    give_up: bool,
    user: str,
    description: str,
    cfg_over: dict[str, Any],
    quality: float,
    train: bool = True,
    evaluate: bool = True,
    display_name: str | None = None,
    class_q: dict[str, float] | None = None,
    range_q: dict[str, float] | None = None,
    parent: str | None = None,
) -> None:
    """A multi-task sparse-detection run logged with raw xflow-style metric names (learned uncertainty weighting:
    ``*/weighted_loss``, ``*/unweighted_loss``, ``*/uncertainty_weight_log_sigma``, ``evaluation/<task>/…``).
    The connector maps them to canonical keys; ``give_up`` makes the velocity head's sigma run away."""
    rng = np.random.default_rng(seed)
    w = _Writer(root, run_id)
    t0 = created.timestamp()
    steps = np.arange(100, 20001, 100)
    prog = steps / steps.max()
    n = lambda sd=0.02: np.exp(rng.normal(0, sd, steps.size))  # noqa: E731
    task = "train_sparse_detection"
    total_w = np.zeros(steps.size)
    total_u = np.zeros(steps.size)
    terms: dict[str, tuple[float, float, float]] = {}  # identity -> (start loss, end factor, sigma end)
    for k in range(6):
        f = 1 + 0.5 * np.exp(-k / 0.8)
        terms[f"d{k}_loss_cls"] = (0.9 * f, 0.45, -0.25 + 0.02 * k)
        terms[f"d{k}_loss_box"] = (1.4 * f, 0.4, -0.1 + 0.02 * k)
    terms["loss_vel"] = (0.8, 0.97 if give_up else 0.55, 1.1 if give_up else 0.1)
    for ident, (l0, end, s_end) in terms.items():
        ul = l0 * (end + (1 - end) * np.exp(-prog * 4)) * n()
        sigma = s_end * (1 - np.exp(-prog * (2.0 if give_up and ident == "loss_vel" else 5.0))) + rng.normal(
            0, 0.005, steps.size
        )
        wl = ul * np.exp(-2 * sigma) + sigma
        w.series(f"{task}/{ident}/unweighted_loss", steps, ul, t0)
        w.series(f"{task}/{ident}/weighted_loss", steps, wl, t0)
        w.series(f"{task}/{ident}/weighted_loss_no_reg", steps, ul * np.exp(-2 * sigma), t0)
        w.series(f"{task}/{ident}/uncertainty_weight_log_sigma", steps, sigma, t0)
        total_w += wl
        total_u += ul
    w.series("train_weighted_total_loss", steps, total_w, t0)
    w.series("train_unweighted_total_loss", steps, total_u, t0)
    w.series("train/learning_rate", steps, 2e-4 * 0.5 * (1 + np.cos(np.pi * prog)), t0)
    w.series("train/time_per_iteration", steps, 1.1 * n(0.05), t0)
    ev = "evaluation/sparse_detection"
    last = int(steps[-1])
    classes = {"PassengerCar": 0.71, "Pedestrian": 0.52, "Cyclist": 0.41, "Truck": 0.48, "Animal": 0.12}
    for c, base in classes.items():
        q = base * quality * (class_q or {}).get(c, 1.0)
        w.scalar(f"{ev}/{c}", last, q, t0)
        for dist, drop in ((40, 1.0), (80, 0.85), (120, 0.66), (160, 0.45)):
            w.scalar(
                f"{ev}/alliance_manual_labels.{c}.pr_curves_detailed.auc{0.9:.7f}{float(dist):.7f}",
                last,
                q * drop * (0.9 if give_up and dist >= 120 else 1.0),
                t0,
            )
        for m, v in (("f1_score", q * 0.95), ("precision", q * 1.05), ("recall", 0.9)):
            w.scalar(f"{ev}/alliance_manual_labels.{c}.working_point.{m}{0.9:.7f}", last, min(v, 1.0), t0)
        w.scalar(
            f"{ev}/alliance_manual_labels.{c}.bb3d_match_evaluation.center_distance_rsme",
            last,
            0.42 / quality * (1.25 if give_up else 1.0),
            t0,
        )
    for r, v in (("Range0-40", 0.66), ("Range40-80", 0.55), ("Range80-120", 0.41), ("Range120-160", 0.30)):
        w.scalar(
            f"{ev}/{r}",
            last,
            v
            * quality
            * (range_q or {}).get(r, 1.0)
            * (0.9 if give_up and r in ("Range80-120", "Range120-160") else 1.0),
            t0,
        )
    w.scalar(f"{ev}/alliance_manual_labels/all/mean_f1_score", last, 0.5 * quality, t0)
    w.metrics = [
        m
        for m in w.metrics
        if (train or json.loads(m)["key"].startswith("evaluation/"))
        and (evaluate or not json.loads(m)["key"].startswith("evaluation/"))
    ]
    cfg = _config(
        {
            "model.name": "sparse_detection_temporal_ground_truth",
            "loss.uncertainty_weighting": True,
            "loss.task_weights": {"cls": 1.0, "box": 1.0, "vel": 1.0},
            **cfg_over,
            "seed": seed,
        }
    )
    log = (
        "\n".join(
            [
                f"[{created.isoformat()}] starting {run_id}",
                *(f"step {int(st)} loss={total_w[i]:.4f}" for i, st in enumerate(steps[::20])),
                "training finished",
            ]
        )
        + "\n"
    )
    w.finish(
        {
            "name": run_id,
            "display_name": display_name or run_id,
            "experiment": "sparse_detection",
            "kind": "training" if train else "evaluation",
            "parent": {"backend": "local", "run_id": parent} if parent else None,
            "status": "completed",
            "created_at": created.isoformat(),
            "started_at": created.isoformat(),
            "ended_at": (created + timedelta(hours=9)).isoformat(),
            "user": user,
            "description": description,
            "tags": {"role": "experiment", "family": "xflow"},
            "params": {"seed": str(seed)},
            "git": {"commit": f"{seed:07x}"[:7], "branch": "exp/uncertainty-weighting"},
            "compute": {"target": "a100-cluster", "gpu_type": "A100-80GB", "gpu_count": 8, "node_count": 1},
            "summary_metrics": {},
        },
        cfg,
        log,
    )
    _xflow_stage_files(
        w.dir, rng, cfg, train=train, evaluate=evaluate, steps=steps, total_w=total_w, quality=quality, class_q=class_q
    )


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
            kit={},
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
            "model.radar_encoder.fpn_levels": 3,
            "model.radar_encoder.lr_mult": 0.02,
            "model.radar_encoder.per_scale_norm": False,
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
        kit={
            "radar_lr_mult": 0.02,
            "radar_scale_collapse": True,
            "radar_contrib": (0.002, 0.004),
            "recall_scale": {"30-60": 0.985, "60-inf": 0.84},
        },
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
        kit={},
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
        kit={},
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
        kit={},
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
        kit={},
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
        kit={},
    )
    ids.append("priya-bs32")
    _detector_run(
        root,
        "sam-radar-live",
        seed=23,
        created=at(0.15),
        cfg_over={
            "model.fusion.modalities": ["camera", "lidar", "radar"],
            "model.radar_encoder.fpn_levels": 3,
            "model.radar_encoder.lr_mult": 1.0,
            "model.radar_encoder.per_scale_norm": True,
        },
        quality={"nds": 0.99},
        tags={"role": "candidate", "modalities": "camera+lidar+radar"},
        user="sam",
        compute="a100-cluster",
        commit="a81d07c",
        branch="feat/radar-fusion",
        description="Radar fusion only (4-frame window) — still training",
        running_frac=0.45,
        kit={},
    )
    ids.append("sam-radar-live")

    # The fix that a study of cand-radar-8f leads to: normal radar LR + per-scale norm, 4-frame window.
    _detector_run(
        root,
        "sam-radar-ms-fix",
        seed=24,
        created=at(1.5),
        cfg_over={
            "model.fusion.modalities": ["camera", "lidar", "radar"],
            "model.radar_encoder.fpn_levels": 3,
            "model.radar_encoder.lr_mult": 1.0,
            "model.radar_encoder.per_scale_norm": True,
        },
        quality={"nds": 1.012, "map": 1.008, "amota": 1.02, "mave": 0.93, "recall_30-60": 1.01, "recall_60-inf": 1.07},
        tags={"role": "candidate", "modalities": "camera+lidar+radar"},
        user="sam",
        compute="a100-cluster",
        commit="b7c1d2e",
        branch="feat/radar-fusion",
        description="Radar multi-scale (3 FPN levels) with per-scale norm, radar lr_mult 1.0, 4-frame window",
        kit={"radar_contrib": (0.025, 0.10), "recall_scale": {"30-60": 1.01, "60-inf": 1.07}},
    )
    ids.append("sam-radar-ms-fix")

    # Streaming (StreamPETR-style) camera model with an uncertainty head: a run with problems, then the fix.
    stream_cfg = {
        "model.fusion.modalities": ["camera"],
        "model.head.type": "streampetr",
        "model.head.uncertainty": "laplace",
        "model.head.dn.num_groups": 5,
        "model.head.dn.noise_scale": 0.1,
        "temporal.memory_queue": 512,
        "temporal.propagated_queries": 128,
    }
    _detector_run(
        root,
        "lee-stream-unc",
        seed=31,
        created=at(3.2),
        cfg_over=stream_cfg,
        quality={"nds": 0.93, "map": 0.94, "mave": 1.25},
        tags={"role": "experiment", "family": "streaming"},
        user="lee",
        compute="a100-cluster",
        commit="c41e9a0",
        branch="exp/stream-uncertainty",
        description="StreamPETR head + Laplace uncertainty (NLL only), DN noise 0.1, 128 propagated queries",
        kit={
            "stream": {
                "attenuation": True,
                "far_overconfidence": 2.3,
                "memory_gain": 0.012,
                "memory_share": 0.06,
                "prop_pos": 0.04,
                "dn_ratio": 0.08,
                "unc_bbox_cos": -0.42,
                "unc_grad": 1.6,
            }
        },
    )
    ids.append("lee-stream-unc")
    _detector_run(
        root,
        "lee-stream-unc-fix",
        seed=32,
        created=at(1.8),
        cfg_over={
            **stream_cfg,
            "model.head.uncertainty_l1_weight": 1.0,
            "model.head.uncertainty_detach_scale": True,
            "model.head.dn.noise_scale": 0.4,
        },
        quality={"nds": 0.985, "map": 0.99, "mave": 0.95},
        tags={"role": "experiment", "family": "streaming"},
        user="lee",
        compute="a100-cluster",
        commit="c52f1b3",
        branch="exp/stream-uncertainty",
        description="Plain L1 next to the NLL with detached scale, DN noise 0.4",
        parent=None,
        kit={"stream": {"far_overconfidence": 1.25, "memory_gain": 0.09, "memory_share": 0.38, "dn_ratio": 0.45}},
    )
    ids.append("lee-stream-unc-fix")

    # xflow-style multi-task run (raw metric names, learned uncertainty weighting): the velocity head is abandoned.
    _xflow_run(
        root,
        "xf-sparse-uw",
        seed=41,
        created=at(2.8),
        give_up=True,
        user="dana",
        description="Sparse detection, learned uncertainty weighting on all terms (log sigma unbounded)",
        cfg_over={"loss.log_sigma_bounds": None},
        quality=0.97,
    )
    _xflow_run(
        root,
        "xf-sparse-uw-bounded",
        seed=42,
        created=at(1.2),
        give_up=False,
        user="dana",
        description="log sigma clamped to [-1, 0.5] for the velocity term",
        cfg_over={"loss.log_sigma_bounds": [-1.0, 0.5]},
        quality=1.0,
    )
    ids += ["xf-sparse-uw", "xf-sparse-uw-bounded"]

    # An xflow-style pipeline: finetune stage + evaluation after pretraining and after finetuning. Metrics live on
    # the stages; opening the pipeline shows them merged (stage=… for the evaluation logged twice).
    pipe_id = "xf-pipe-finetune"
    stage_runs = [
        ("xf-pipe-finetune-train", "train_finetune", {"train": True, "evaluate": False}),
        ("xf-pipe-finetune-eval-pre", "evaluate_pretrain", {"train": False, "evaluate": True, "quality": 0.95}),
        (
            "xf-pipe-finetune-eval-ft",
            "evaluate_finetune",
            {
                "train": False,
                "evaluate": True,
                "quality": 1.0,
                "class_q": {"Pedestrian": 0.86, "Animal": 0.7},
                "range_q": {"Range120-160": 0.9},
            },
        ),
    ]
    for i, (rid, stage, extra) in enumerate(stage_runs):
        _xflow_run(
            root,
            rid,
            seed=51 + i,
            created=at(0.9 - i * 0.05),
            give_up=False,
            user="dana",
            description=f"{stage} stage of {pipe_id}",
            cfg_over={"stage": stage},
            display_name=stage,
            parent=pipe_id,
            **{"quality": 1.0, **extra},
        )
    pdir = root / pipe_id
    (pdir / "logs").mkdir(parents=True, exist_ok=True)
    (pdir / "run.json").write_text(
        json.dumps(
            {
                "name": pipe_id,
                "display_name": "sparse_detection_temporal finetune",
                "experiment": "sparse_detection",
                "kind": "pipeline",
                "status": "completed",
                "created_at": at(0.9).isoformat(),
                "started_at": at(0.9).isoformat(),
                "ended_at": at(0.4).isoformat(),
                "user": "dana",
                "description": "Finetune on the new label set",
                "tags": {"role": "experiment", "family": "xflow"},
                "children": [r for r, _, _ in stage_runs],
                "git": {"commit": "e1f2a3b", "branch": "exp/finetune"},
            },
            indent=2,
        )
    )
    ids += [pipe_id, *(r for r, _, _ in stage_runs)]

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
        ),
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
        kit={},
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
