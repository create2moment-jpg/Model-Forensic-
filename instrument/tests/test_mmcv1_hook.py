"""Run a real mmcv 1.x IterBasedRunner with ForensicMmcvHook (skipped if mmcv 1.x is not installed)."""

import json
import logging

import pytest

torch = pytest.importorskip("torch")
mmcv_runner = pytest.importorskip("mmcv.runner")

from model_forensic_instrument.integrations.mmcv1_hook import ForensicMmcvHook, map_mmdet3d_metric  # noqa: E402
from tests.conftest import ToyFusionDetector  # noqa: E402


class Mmcv1Toy(torch.nn.Module):
    """mmcv 1.x models implement train_step and return loss, log_vars and num_samples."""

    def __init__(self) -> None:
        super().__init__()
        self.net = ToyFusionDetector()

    def train_step(self, data, optimizer=None, **kwargs):
        losses = self.net(*data)
        loss = sum(losses.values())
        log_vars = {k: float(v.detach()) for k, v in losses.items()}
        log_vars["loss"] = float(loss.detach())
        return {"loss": loss, "log_vars": log_vars, "num_samples": data[0].shape[0]}


def loader():
    g = torch.Generator().manual_seed(0)
    return [
        (
            torch.randn(4, 3, 16, 16, generator=g),
            torch.randn(4, 2, 16, 16, generator=g),
            torch.randint(0, 3, (4,), generator=g),
        )
        for _ in range(8)
    ]


def test_mmcv1_runner_logs_grads_updates_features_and_layer_losses(tmp_path) -> None:
    torch.manual_seed(0)
    model = Mmcv1Toy()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.05)
    runner = mmcv_runner.IterBasedRunner(
        model, optimizer=optimizer, work_dir=str(tmp_path), logger=logging.getLogger("mmcv1-test"), max_iters=6
    )
    runner.register_training_hooks(lr_config=None, optimizer_config=dict(grad_clip=None))
    out = tmp_path / "metrics.jsonl"
    runner.register_hook(
        ForensicMmcvHook(
            groups=dict(
                camera="net.img_backbone", radar=["net.radar_encoder", "net.radar_neck"], decoder="net.decoder"
            ),
            features=dict(radar_fpn="net.radar_neck"),
            every=2,
            jsonl_path=str(out),
        ),
        priority="LOW",
    )
    runner.run([loader()], [("train", 1)])

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    names = {r["key"] for r in rows}
    assert {"train/grad_norm[module=radar]", "train/update_ratio[module=camera]"} <= names
    assert any(n.startswith("train/feat_rms[module=radar_fpn,scale=") for n in names)
    assert {"train/loss/cls[layer=0]", "train/loss/cls[layer=2]", "train/loss"} <= names
    upd = [r["value"] for r in rows if r["key"] == "train/update_ratio[module=decoder]"]
    assert upd and all(u > 0 for u in upd)  # the hook saw the real optimizer step


def test_map_mmdet3d_metric() -> None:
    assert map_mmdet3d_metric("pts_bbox_NuScenes/NDS") == "val/NDS"
    assert map_mmdet3d_metric("pts_bbox_NuScenes/car_AP_dist_2.0") == "val/AP[class=car,thr=2.0]"
    assert map_mmdet3d_metric("pts_bbox_NuScenes/pedestrian_vel_err") == "val/AVE[class=pedestrian]"
