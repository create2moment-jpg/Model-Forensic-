"""Run a real MMEngine Runner with ForensicHook (skipped if mmengine is not installed)."""

import json

import pytest

torch = pytest.importorskip("torch")
mmengine = pytest.importorskip("mmengine")

from mmengine.model import BaseModel  # noqa: E402
from mmengine.runner import Runner  # noqa: E402

import model_forensic_instrument.integrations.mmengine_hook  # noqa: E402,F401  (registers ForensicHook)
from tests.conftest import ToyFusionDetector  # noqa: E402


class MMToy(BaseModel):
    def __init__(self) -> None:
        super().__init__()
        self.net = ToyFusionDetector()

    def forward(self, img, radar, target, mode="loss"):
        return self.net(img, radar, target)


class DS(torch.utils.data.Dataset):
    def __len__(self) -> int:
        return 32

    def __getitem__(self, i):
        g = torch.Generator().manual_seed(i)
        return {
            "img": torch.randn(3, 16, 16, generator=g),
            "radar": torch.randn(2, 16, 16, generator=g),
            "target": torch.randint(0, 3, (), generator=g),
        }


def test_hook_logs_grads_updates_features_and_layer_losses(tmp_path) -> None:
    out = tmp_path / "metrics.jsonl"
    runner = Runner(
        model=MMToy(),
        work_dir=str(tmp_path / "work"),
        train_dataloader=dict(
            dataset=DS(),
            batch_size=4,
            sampler=dict(type="DefaultSampler", shuffle=False),
            collate_fn=dict(type="default_collate"),
        ),
        optim_wrapper=dict(optimizer=dict(type="AdamW", lr=1e-3)),
        train_cfg=dict(by_epoch=False, max_iters=6),
        custom_hooks=[
            dict(
                type="ForensicHook",
                every=2,
                feature_every=2,
                jsonl_path=str(out),
                groups=dict(
                    camera="net.img_backbone",
                    radar=["net.radar_encoder", "net.radar_neck"],
                    fusion="net.fusion",
                    decoder="net.decoder",
                ),
                features=dict(radar_fpn="net.radar_neck"),
            )
        ],
        default_hooks=dict(checkpoint=None),
        default_scope="mmengine",
    )
    runner.train()
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    keys = {r["key"] for r in rows}
    assert "train/grad_norm[module=radar]" in keys and "train/update_ratio[module=decoder]" in keys
    assert "train/feat_rms[module=radar_fpn,scale=2]" in keys
    assert "train/loss/cls[layer=2]" in keys
    upd = [r["value"] for r in rows if r["key"] == "train/update_ratio[module=camera]"]
    assert upd and all(v > 0 for v in upd)  # measured around the real optimizer step
