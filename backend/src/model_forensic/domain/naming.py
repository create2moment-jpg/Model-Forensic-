"""Pure rules about metric names that the analyses and the connectors share (no I/O)."""

from __future__ import annotations


def loss_family(term: str, dims: dict[str, str] | tuple[tuple[str, str], ...]) -> str:
    """Group a per-term loss into the families a loss ledger reads: decoder_cls / decoder_bbox (matched queries),
    denoise_cls / denoise_bbox (DN queries), camera / radar (auxiliary heads), other."""
    d = dict(dims)
    t = term.lower()
    if t.startswith(("camera", "cam_", "img")):
        return "camera"
    if t.startswith("radar"):
        return "radar"
    kind = (
        "cls"
        if any(x in t for x in ("cls", "class", "focal"))
        else ("bbox" if any(x in t for x in ("bbox", "box", "reg", "iou", "l1", "center", "vel")) else "")
    )
    if not kind:
        return "other"
    return f"{'denoise' if d.get('branch') == 'dn' else 'decoder'}_{kind}"
