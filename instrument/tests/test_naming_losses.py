from model_forensic_instrument import decode, encode, metric_name, normalize_losses
from model_forensic_instrument.naming import with_dims


def test_metric_name_and_mlflow_roundtrip() -> None:
    n = metric_name("recall", "val", range="60-inf", **{"class": "traffic_cone"})
    assert n == "val/recall[class=traffic_cone,range=60-inf]"
    e = encode(n)
    assert e == "val/recall__class_traffic_cone__range_60-inf"
    assert all(ch.isalnum() or ch in "_./- :" for ch in e)  # MLflow-legal
    assert decode(e) == ("val/recall", {"class": "traffic_cone", "range": "60-inf"})


def test_with_dims_merges() -> None:
    assert with_dims("val/recall[range=60-inf]", ablation="no_radar") == "val/recall[ablation=no_radar,range=60-inf]"
    assert with_dims("val/NDS", ablation="none") == "val/NDS[ablation=none]"


def test_mmdet_style_decoder_losses() -> None:
    out = normalize_losses(
        {
            "d0.loss_cls": 1.0,
            "d1.loss_cls": 0.8,
            "loss_cls": 0.6,
            "d0.loss_bbox": 0.5,
            "loss_bbox": 0.3,
            "dn_loss_cls": 0.2,
            "loss_depth": 0.1,
            "acc": 0.9,
        }
    )
    assert out["train/loss/cls[layer=0]"] == 1.0 and out["train/loss/cls[layer=2]"] == 0.6
    assert out["train/loss/bbox[layer=1]"] == 0.3  # unprefixed = last layer of that term
    assert abs(out["train/loss/cls"] - 2.4) < 1e-9 and out["train/loss/depth"] == 0.1
    assert out["train/loss/dn_cls"] == 0.2 and "train/loss/cls[branch=dn]" in out
    assert abs(out["train/loss"] - 3.5) < 1e-9 and not any("acc" in k for k in out)


def test_suffix_indexed_losses() -> None:
    out = normalize_losses({"loss_cls_0": 1.0, "loss_cls_1": 0.7, "loss_box_0": 0.4, "loss": 2.1})
    assert out["train/loss/cls[layer=1]"] == 0.7 and out["train/loss/bbox[layer=0]"] == 0.4
    assert out["train/loss"] == 2.1  # reported total wins
