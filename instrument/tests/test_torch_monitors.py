import pytest

from model_forensic_instrument import Forensic, MemorySink, ablation_eval, zero_output
from model_forensic_instrument.torch_monitors import resolve_groups

torch = pytest.importorskip("torch")

GROUPS = {"camera": "img_backbone", "radar": ["radar_encoder", "radar_neck"], "fusion": "fusion", "decoder": "decoder"}


def train(model, batch, sink, steps=6, radar_lr=1e-3):
    params = [
        {"params": [p for n, p in model.named_parameters() if not n.startswith("radar")], "lr": 1e-3},
        {"params": [p for n, p in model.named_parameters() if n.startswith("radar")], "lr": radar_lr},
    ]
    opt = torch.optim.AdamW(params)
    fx = Forensic(
        model,
        groups=GROUPS,
        features={"radar_fpn": "radar_neck", "fused": "fusion"},
        every=2,
        feature_every=2,
        sink=sink,
    )
    for step in range(steps):
        fx.start_step(step)
        losses = model(*batch)
        sum(losses.values()).backward()
        fx.after_backward(step)
        opt.step()
        opt.zero_grad()
        fx.after_step(step)
        fx.log_losses(losses, step)
    fx.close()
    return fx


def test_groups_cover_all_params(model) -> None:
    groups = resolve_groups(model, GROUPS)
    assert set(groups) == {"camera", "radar", "fusion", "decoder", "other"}  # cls head → other
    with pytest.raises(ValueError, match="matched no trainable parameters"):
        resolve_groups(model, {"lidar": "pts_backbone"})


def test_grad_and_update_metrics(model, batch) -> None:
    sink = MemorySink()
    train(model, batch, sink, radar_lr=0.0)  # radar gets gradients but never moves
    names = sink.names()
    for g in ("camera", "radar", "fusion", "decoder"):
        assert f"train/grad_norm[module={g}]" in names and f"train/update_ratio[module={g}]" in names
    assert [s for s, _ in sink.series("train/grad_norm[module=radar]")] == [0, 2, 4]
    radar_upd = [v for _, v in sink.series("train/update_ratio[module=radar]")]
    cam_upd = [v for _, v in sink.series("train/update_ratio[module=camera]")]
    assert max(radar_upd) == 0.0 and min(cam_upd) > 1e-5
    assert all(v > 0 for _, v in sink.series("train/grad_norm[module=radar]"))


def test_frozen_module_is_dead(model, batch) -> None:
    sink = MemorySink()
    # Detach the radar path: radar params get no gradient at all.
    orig = model.radar_neck.forward
    model.radar_neck.forward = lambda x: [t.detach() for t in orig(x)]
    train(model, batch, sink)
    assert all(v == 1.0 for _, v in sink.series("train/grad_dead_frac[module=radar]"))
    assert all(v == 0.0 for _, v in sink.series("train/grad_norm[module=radar]"))


def test_feature_stats_per_scale(model, batch) -> None:
    sink = MemorySink()
    train(model, batch, sink)
    for s in (0, 1, 2):
        assert sink.series(f"train/feat_rms[module=radar_fpn,scale={s}]")
    assert sink.series("train/feat_std[module=fused]")
    fz = [v for _, v in sink.series("train/feat_zero_frac[module=radar_fpn,scale=0]")]
    assert all(0.0 <= v <= 1.0 for v in fz)  # ReLU output → some exact zeros


def test_decoder_layer_losses_logged(model, batch) -> None:
    sink = MemorySink()
    train(model, batch, sink)
    assert {"train/loss/cls[layer=0]", "train/loss/cls[layer=1]", "train/loss/cls[layer=2]", "train/loss"} <= set(
        sink.names()
    )


def test_ablation_eval_zeroes_branch(model, batch) -> None:
    sink = MemorySink()
    model.eval()

    def evaluate():
        with torch.no_grad():
            losses = model(*batch)
        return {"loss_cls": float(losses["loss_cls"]), "recall[range=60-inf]": 0.5}

    res = ablation_eval(model, evaluate, {"no_radar": ["radar_neck"], "no_camera": "img_backbone"}, sink=sink, step=10)
    assert set(res) == {"none", "no_radar", "no_camera"}
    assert res["no_radar"]["loss_cls"] != res["none"]["loss_cls"]
    assert "val/recall[ablation=no_radar,range=60-inf]" in sink.names()
    # hooks are removed afterwards
    with torch.no_grad():
        assert float(model(*batch)["loss_cls"]) == res["none"]["loss_cls"]


def test_zero_output_handles_multiscale_lists(model, batch) -> None:
    with zero_output(model, "radar_neck"), torch.no_grad():
        outs = model.radar_neck(model.radar_encoder(batch[1]))
    assert isinstance(outs, list) and all(float(o.abs().sum()) == 0 for o in outs)


def test_amp_grad_scale_is_divided(model, batch) -> None:
    s1, s2 = MemorySink(), MemorySink()
    for sink, scale in ((s1, None), (s2, lambda: 1024.0)):
        torch.manual_seed(0)
        from tests.conftest import ToyFusionDetector

        m = ToyFusionDetector()
        fx = Forensic(m, groups=GROUPS, every=1, sink=sink, grad_scale=scale, track_updates=False)
        losses = m(*batch)
        (sum(losses.values()) * (1024.0 if scale else 1.0)).backward()
        fx.after_backward(0)
    a = s1.series("train/grad_norm[module=camera]")[0][1]
    b = s2.series("train/grad_norm[module=camera]")[0][1]
    assert abs(a - b) / a < 1e-4
