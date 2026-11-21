"""Uncertainty stats, temporal/query diagnostics, loss-gradient probe, StreamPETR loss names, mmcv-1.x hook."""

import math
from types import SimpleNamespace

import pytest

from model_forensic_instrument import (
    Forensic,
    LossGradProbe,
    MemorySink,
    SequencePositionMeter,
    normalize_losses,
    query_source_stats,
    uncertainty_stats,
)
from model_forensic_instrument.integrations.mmcv1_hook import ForensicMmcvHook, map_mmdet3d_metric

torch = pytest.importorskip("torch")


def test_streampetr_loss_names() -> None:
    losses = {
        "loss_cls": 0.5,
        "loss_bbox": 0.8,
        "d0.loss_cls": 0.9,
        "d4.loss_bbox": 0.85,
        "dn_loss_cls": 0.2,
        "dn_loss_bbox": 0.3,
        "d0.dn_loss_cls": 0.4,
        "loss_cls2d": 0.6,
        "loss_bbox2d": 0.2,
        "loss_iou2d": 0.3,
        "loss_centers2d": 0.1,
        "loss_centerness": 0.05,
        "loss_depth": 0.7,
    }
    out = normalize_losses(losses)
    assert out["train/loss/cls[layer=1]"] == 0.5  # last main layer after d0
    assert out["train/loss/cls[branch=dn,layer=1]"] == 0.2 and out["train/loss/cls[branch=dn,layer=0]"] == 0.4
    assert out["train/loss/aux2d_cls"] == 0.6 and out["train/loss/aux2d_centers"] == 0.1
    assert out["train/loss/aux2d_centerness"] == 0.05 and out["train/loss/depth"] == 0.7
    assert abs(out["train/loss"] - sum(losses.values())) < 1e-9


def test_uncertainty_stats_calibrated_vs_overconfident() -> None:
    torch.manual_seed(0)
    n = 20000
    b = torch.rand(n, 2) * 0.5 + 0.1  # predicted Laplace scale
    r = torch.distributions.Laplace(torch.zeros(n, 2), b).sample()  # residuals drawn from that scale → calibrated
    dist = torch.rand(n) * 80
    out = uncertainty_stats(
        r, b, kind="laplace", dims=["x", "vx"], slices={"0-30": dist < 30, "60-inf": dist >= 60}, clamp=(0.1, 0.6)
    )
    assert abs(out["val/unc_nerr[dim=x]"] - 1.0) < 0.05
    assert (
        abs(out["val/unc_cov[dim=x,level=68]"] - 0.68) < 0.02 and abs(out["val/unc_cov[dim=x,level=95]"] - 0.95) < 0.01
    )
    assert out["val/unc_corr[dim=x]"] > 0.3  # scale ranks errors
    assert "val/unc_nerr[dim=vx,range=60-inf]" in out and "val/unc_clamped_frac[dim=x]" in out
    over = uncertainty_stats(r * 2, b, kind="laplace", dims=["x", "vx"])  # errors twice the claimed scale
    assert over["val/unc_nerr[dim=x]"] > 1.8 and over["val/unc_cov[dim=x,level=68]"] < 0.5


def test_uncertainty_gaussian_log_variance() -> None:
    torch.manual_seed(1)
    sigma = torch.full((5000, 1), 0.5)
    r = torch.randn(5000, 1) * 0.5
    out = uncertainty_stats(r, (sigma**2).log(), kind="gaussian", log_variance=True)
    assert abs(out["val/unc_nerr[dim=0]"] - 1.0) < 0.06 and abs(out["val/unc_scale[dim=0]"] - 0.5) < 1e-4
    assert "val/unc_corr[dim=0]" not in out or math.isnan(out["val/unc_corr[dim=0]"]) is False


def test_sequence_position_meter() -> None:
    m = SequencePositionMeter()
    for pos, loss in [(0, 2.0), (0, 2.2), (1, 1.6), (5, 1.4), (6, 1.2)]:
        m.add({"loss_cls": loss}, pos)
    out = m.flush()
    assert abs(out["train/loss/cls[seqpos=0]"] - 2.1) < 1e-9 and abs(out["train/loss/cls[seqpos=4-inf]"] - 1.3) < 1e-9
    assert out["train/loss/cls[seqpos=1-3]"] == 1.6 and m.flush() == {}


def test_query_source_stats() -> None:
    prop = torch.tensor([True] * 4 + [False] * 6)
    pos = torch.tensor([True, True, False, False, True, False, False, False, False, False])
    out = query_source_stats(pos, prop)
    assert out["train/q_pos_frac[source=propagated]"] == 0.5 and abs(out["train/q_pos_frac[source=new]"] - 1 / 6) < 1e-9
    assert abs(out["train/q_from_memory_frac"] - 2 / 3) < 1e-9


def test_loss_grad_probe_detects_conflict() -> None:
    torch.manual_seed(0)
    trunk = torch.nn.Linear(4, 4)
    model = torch.nn.ModuleDict({"trunk": trunk, "head": torch.nn.Linear(4, 1)})
    x = torch.randn(16, 4)
    h = model["trunk"](x)
    losses = {
        "loss_bbox": h.sum(),
        "loss_uncertainty": -h.sum() * 0.5,
        "loss_cls": model["head"](h.detach()).pow(2).mean(),
    }
    out = LossGradProbe(model, "trunk", every=1).probe(losses, 0)
    assert out["train/term_grad_cos[a=bbox,b=uncertainty]"] < -0.99  # pull in opposite directions
    assert "train/term_grad_norm[term=cls]" not in out  # no gradient on the shared trunk → not reported


def test_forensic_facade_streaming(model, batch) -> None:
    sink = MemorySink()
    fx = Forensic(model, groups={"camera": "img_backbone"}, every=2, sink=sink, probe_shared="fusion", probe_every=2)
    for step in range(4):
        losses = model(*batch)
        fx.probe_losses(losses, step)
        sum(losses.values()).backward()
        fx.log_losses(losses, step, seqpos=step)
        fx.log_query_sources(torch.tensor([True, False]), torch.tensor([True, False]), step)
        model.zero_grad()
    names = sink.names()
    assert "train/loss/cls[seqpos=0]" in names and "train/q_from_memory_frac" in names
    assert any(n.startswith("train/term_grad_cos[") for n in names)


def test_map_mmdet3d_metric() -> None:
    assert map_mmdet3d_metric("pts_bbox_NuScenes/NDS") == "val/NDS"
    assert map_mmdet3d_metric("pts_bbox_NuScenes/car_AP_dist_2.0") == "val/AP[class=car,thr=2.0]"
    assert map_mmdet3d_metric("pts_bbox_NuScenes/traffic_cone_trans_err") == "val/ATE[class=traffic_cone]"
    assert map_mmdet3d_metric("img_bbox/loss") is None


def test_mmcv1_hook_with_fake_runner(model, batch, tmp_path) -> None:
    """Simulates mmcv 1.x: OptimizerHook does zero_grad/backward/step inside after_train_iter, before this hook."""
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
    runner = SimpleNamespace(model=model, hooks=[], iter=0, outputs={}, log_buffer=SimpleNamespace(output={}))
    hook = ForensicMmcvHook(
        groups={"camera": "img_backbone", "decoder": "decoder"}, every=1, jsonl_path=str(tmp_path / "m.jsonl")
    )
    hook.fx = None
    hook.before_run(runner)
    sink = MemorySink()
    hook.fx.sink = sink
    for it in range(3):
        runner.iter = it
        hook.before_train_iter(runner)
        losses = model(*batch)
        opt.zero_grad()
        sum(losses.values()).backward()
        opt.step()  # ← OptimizerHook
        runner.outputs = {"log_vars": {k: float(v.detach()) for k, v in losses.items()}}
        if it == 2:
            runner.log_buffer.output = {"pts_bbox_NuScenes/NDS": 0.55, "pts_bbox_NuScenes/car_AP_dist_1.0": 0.6}
        hook.after_train_iter(runner)
    assert all(v > 0 for _, v in sink.series("train/update_ratio[module=camera]"))
    assert sink.series("train/grad_norm[module=decoder]") and sink.series("train/loss/cls[layer=2]")
    assert sink.series("val/NDS") == [(2, 0.55)] and sink.series("val/AP[class=car,thr=1.0]")
