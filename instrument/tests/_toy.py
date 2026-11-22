"""Toy camera + radar detector for the kit tests (needs torch)."""

import torch

nn = torch.nn


class RadarFPN(nn.Module):
    """Multi-scale radar neck: returns a list of 3 feature maps (like an FPN)."""

    def __init__(self) -> None:
        super().__init__()
        self.convs = nn.ModuleList([nn.Conv2d(4, 8, 3, stride=s, padding=1) for s in (1, 2, 4)])

    def forward(self, x):
        return [torch.relu(c(x)) for c in self.convs]


class ToyFusionDetector(nn.Module):
    """Camera + radar (multi-scale) → fusion → 3-layer decoder with per-layer losses."""

    def __init__(self) -> None:
        super().__init__()
        self.img_backbone = nn.Sequential(nn.Conv2d(3, 8, 3, padding=1), nn.ReLU())
        self.radar_encoder = nn.Conv2d(2, 4, 1)
        self.radar_neck = RadarFPN()
        self.fusion = nn.Conv2d(16, 16, 1)
        self.decoder = nn.ModuleList([nn.Linear(16, 16) for _ in range(3)])
        self.cls = nn.Linear(16, 3)

    def forward(self, img, radar, target):
        cam = self.img_backbone(img)
        r = self.radar_neck(self.radar_encoder(radar))
        r0 = (
            r[0]
            + nn.functional.interpolate(r[1], size=r[0].shape[-2:])
            + nn.functional.interpolate(r[2], size=r[0].shape[-2:])
        )
        fused = self.fusion(torch.cat([cam, r0], 1)).mean((2, 3))
        losses = {}
        h = fused
        for i, layer in enumerate(self.decoder):
            h = torch.relu(layer(h))
            losses[f"d{i}.loss_cls" if i < 2 else "loss_cls"] = nn.functional.cross_entropy(self.cls(h), target)
        losses["loss_bbox"] = fused.pow(2).mean() * 0.01
        return losses
