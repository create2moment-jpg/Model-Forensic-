"""Raw metric names (canonical, MLflow-safe, generic and the xflow multi-task layout) → canonical keys."""

import pytest

from model_forensic.infrastructure.connectors.naming import canonical_key

CASES = {
    # canonical / MLflow-safe pass through
    "val/recall[range=60-inf]": "val/recall[range=60-inf]",
    "val/recall__range_60-inf": "val/recall[range=60-inf]",
    "train/loss": "train/loss",
    "train/time_per_iteration": "train/time_per_iteration",
    # generic prefixes
    "train_loss": "train/loss",
    "val_mAP": "val/mAP",
    "eval/accuracy": "val/accuracy",
    "train/total_loss": "train/loss",
    # xflow totals
    "train_total_loss": "train/loss",
    "train_unweighted_total_loss": "train/uloss",
    "train_weighted_total_loss_no_reg": "train/loss_noreg",
    # xflow per-term families
    "train_sparse_detection/d3_loss_cls/weighted_loss": "train/loss/cls[layer=3,task=sparse_detection]",
    "train_sparse_detection/d3_loss_box/unweighted_loss": "train/uloss/box[layer=3,task=sparse_detection]",
    "train_sparse_detection/d3_loss_cls/weighted_loss_no_reg": "train/loss_noreg/cls[layer=3,task=sparse_detection]",
    "train_sparse_detection/d3_loss_box/uncertainty_weight_log_sigma": "train/task_log_sigma[layer=3,task=sparse_detection,term=box]",
    "train_sparse_detection/d3_loss_box": "train/loss_raw/box[layer=3,task=sparse_detection]",
    "train_sparse_detection/odd/weighted_loss": "train/loss/odd[task=sparse_detection]",
    "train_sparse_detection/d0_loss_cls_dn/unweighted_loss": "train/uloss/cls[branch=dn,layer=0,task=sparse_detection]",
    "train_head/weighted_loss": "train/loss[task=head]",
    "train_head/uncertainty_weight_log_sigma": "train/task_log_sigma[task=head,term=head]",
    "train_sparse_detection/d2_dn_loss_bbox/weighted_loss_no_reg": "train/loss_noreg/bbox[branch=dn,layer=2,task=sparse_detection]",
    "train_sparse_detection/radar_head_class_loss/uncertainty_weight_log_sigma": "train/task_log_sigma[task=sparse_detection,term=radar_head_class_loss]",
    # xflow evaluation
    "evaluation/sparse_detection/PassengerCar": "val/score[class=PassengerCar,task=sparse_detection]",
    "evaluation/sparse_detection/AP": "val/AP[task=sparse_detection]",
    "evaluation/sparse_detection/Range0-40": "val/score[range=0-40,task=sparse_detection]",
    "evaluation/sparse_detection/Range300": "val/score[range=300-inf,task=sparse_detection]",
    "evaluation/sparse_detection/alliance_manual_labels.Car.pr_curves_detailed.auc0.800000040.0000000": "val/pr_auc[class=Car,dist=40,recall=0.8,task=sparse_detection]",
    "evaluation/sparse_detection/alliance_manual_labels.PassengerCar.pr_curves_detailed.auc0.9000000160.0000000": "val/pr_auc[class=PassengerCar,dist=160,recall=0.9,task=sparse_detection]",
    "evaluation/sparse_detection/alliance_manual_labels.PassengerCar.pr_curves_detailed.auc@0.9@40": "val/pr_auc[class=PassengerCar,dist=40,recall=0.9,task=sparse_detection]",
    "evaluation/sparse_detection/Car.working_point.f1_score0.9000000": "val/wp_f1_score[class=Car,recall=0.9,task=sparse_detection]",
    "evaluation/sparse_detection/alliance_manual_labels.Pedestrian.working_point.f1_score0.9500000": "val/wp_f1_score[class=Pedestrian,recall=0.95,task=sparse_detection]",
    "evaluation/sparse_detection/alliance_manual_labels.Pedestrian.bb3d_match_evaluation.center_distance_rsme": "val/match_center_distance_rsme[class=Pedestrian,task=sparse_detection]",
    "evaluation/sparse_detection/alliance_manual_labels/all/mean_f1_score": "val/mean_f1_score[task=sparse_detection]",
    "evaluation/sparse_detection/Animal/idf1": "val/idf1[class=Animal,task=sparse_detection]",
    "evaluation/fold-3/accuracy": "val/accuracy[fold=3]",
    "evaluation/other_metric": "val/other_metric",
}


@pytest.mark.parametrize(("raw", "expected"), CASES.items())
def test_canonical_key(raw: str, expected: str) -> None:
    assert str(canonical_key(raw)) == expected


def test_never_raises_on_odd_names() -> None:
    for raw in ["", "a[b", "evaluation/", "train_x/", "train_sparse_detection/a/b/c", "weird name with spaces"]:
        canonical_key(raw)


def test_loss_family() -> None:
    from model_forensic.infrastructure.connectors.naming import loss_family

    assert loss_family("cls", {"layer": "0"}) == "decoder_cls"
    assert loss_family("bbox", {"branch": "dn"}) == "denoise_bbox"
    assert loss_family("camera_head_0_center_loss", {}) == "camera"
    assert loss_family("radar_head_width_loss", {}) == "radar"
