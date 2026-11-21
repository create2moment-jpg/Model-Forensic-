"""Plain PyTorch training loop with the Model Forensic instrumentation kit.

Inside an Azure ML job, `Forensic` logs through MLflow automatically (the job's MLflow run).
Locally it writes outputs/forensic/metrics.jsonl. Run: python examples/train_loop.py
"""

import torch
from torch import nn

from model_forensic_instrument import Forensic, ablation_eval, log_slices


class TinyFusion(nn.Module):  # stand-in for your detector
    def __init__(self) -> None:
        super().__init__()
        self.img_backbone = nn.Sequential(nn.Conv2d(3, 16, 3, padding=1), nn.ReLU())
        self.radar_encoder = nn.Conv2d(2, 8, 1)
        self.radar_neck = nn.ModuleList([nn.Conv2d(8, 16, 3, stride=s, padding=1) for s in (1, 2, 4)])  # multi-scale
        self.fusion = nn.Conv2d(32, 32, 1)
        self.decoder = nn.ModuleList([nn.Linear(32, 32) for _ in range(4)])
        self.cls = nn.Linear(32, 10)

    def radar_features(self, radar):
        x = self.radar_encoder(radar)
        return [torch.relu(conv(x)) for conv in self.radar_neck]

    def forward(self, img, radar, target):
        cam = self.img_backbone(img)
        scales = self.radar_features(radar)
        r = sum(nn.functional.interpolate(s, size=cam.shape[-2:]) for s in scales)
        h = self.fusion(torch.cat([cam, r], 1)).mean((2, 3))
        losses = {}
        for i, layer in enumerate(self.decoder):  # MMDet-style naming: d0..d2 + unprefixed last layer
            h = torch.relu(layer(h))
            losses["loss_cls" if i == len(self.decoder) - 1 else f"d{i}.loss_cls"] = nn.functional.cross_entropy(
                self.cls(h), target
            )
        return losses


def main() -> None:
    model = TinyFusion()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
    fx = Forensic(
        model,
        groups={
            "camera": "img_backbone",
            "radar": ["radar_encoder", "radar_neck"],
            "fusion": "fusion",
            "decoder": "decoder",
        },
        features={"radar_fpn": "radar_neck.0", "fused": "fusion"},  # any module path from model.named_modules()
        every=10,
    )
    for step in range(100):
        img, radar, target = torch.randn(8, 3, 32, 32), torch.randn(8, 2, 32, 32), torch.randint(0, 10, (8,))
        fx.start_step(step)
        losses = model(img, radar, target)
        sum(losses.values()).backward()
        fx.after_backward(
            step
        )  # with fp16 AMP: call after scaler.unscale_(opt), or pass grad_scale=lambda: scaler.get_scale()
        opt.step()
        opt.zero_grad()
        fx.after_step(step)
        fx.log_losses(losses, step)

    # Evaluation: headline + per-range slices, then the same evaluation with each branch zeroed.
    model.eval()

    def evaluate() -> dict:
        with torch.no_grad():
            loss = model(torch.randn(8, 3, 32, 32), torch.randn(8, 2, 32, 32), torch.randint(0, 10, (8,)))["loss_cls"]
        return {"NDS": 1.0 / (1.0 + float(loss)), "recall[range=60-inf]": 0.4}  # replace with your evaluator

    log_slices(fx.sink, 100, "recall", {"0-30": 0.78, "30-60": 0.62, "60-inf": 0.41})
    ablation_eval(model, evaluate, {"no_radar": "radar_neck", "no_camera": "img_backbone"}, sink=fx.sink, step=100)
    fx.close()


if __name__ == "__main__":
    main()
