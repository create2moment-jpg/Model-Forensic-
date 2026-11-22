"""Precision & recall deep dive (v1 rules): target ≠ measured recall, AUC by distance, counts, logs, file guide."""

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.application.precision_recall import auc_rows, guide, ordered_classes, working_points
from model_forensic.domain.metrics import MetricKey, MetricSeries


def ser(key: str, v: float) -> MetricSeries:
    return MetricSeries(key=MetricKey.parse(key), steps=(1.0,), values=(v,))


def test_working_points_keep_target_and_measured_recall_apart() -> None:
    rows = working_points(
        [
            ser("val/wp_precision[class=Ped,recall=0.9,stage=eval]", 0.43),
            ser("val/wp_recall[class=Ped,recall=0.9,stage=eval]", 0.80),
            ser("val/wp_f1_score[class=Ped,recall=0.9,stage=eval]", 0.56),
            ser("val/wp_recall[class=Car,recall=0.9,stage=eval]", 0.95),
            ser("val/score[class=Car,stage=eval]", 0.7),  # not a working point
        ]
    )
    by = {r["class"]: r for r in rows}
    assert by["Ped"]["target_recall"] == 0.9 and by["Ped"]["recall"] == 0.8
    assert abs(by["Ped"]["shortfall"] - 0.1) < 1e-9 and by["Car"]["shortfall"] == 0.0
    assert by["Ped"]["precision"] == 0.43 and by["Ped"]["f1"] == 0.56


def test_auc_rows_and_class_order() -> None:
    rows = auc_rows(
        [
            ser("val/pr_auc[class=Ped,dist=40,recall=0.9,stage=eval]", 0.5),
            ser("val/pr_auc[class=Ped,dist=80,recall=0.9,stage=eval]", 0.4),
        ]
    )
    assert [r["distance_m"] for r in rows] == [40.0, 80.0]
    assert ordered_classes(["Animal", "truck", "Car", "Animal"], ["animal"]) == ["Car", "truck", "Animal"]


def test_artifact_guide_matches_v1() -> None:
    base = "evaluation/alliance_manual_labels/Pedestrian/"
    assert guide(base + "pr_curves_detailed/x.json")["family"] == "distance-stratified PR"
    assert guide(base + "pr_curves/x.json")["family"] == "pr curve"
    assert guide(base + "working_point/x.json")["family"] == "working point"
    assert guide(base + "false_positives_analysis/x.json")["class"] == "Pedestrian"
    assert guide("summaries/train/metrics.csv")["family"] == "scalar metrics"
    assert guide("visualizations/a.png")["family"] == "evaluation plots"
    assert guide("evaluation/odd.bin")["family"] == "other evaluation output"


def test_api_pages(container) -> None:
    c = TestClient(create_app(container))
    run = "local:xf-pipe-finetune"
    d = c.get(f"/api/v1/runs/{run}/precision-recall").json()
    assert d["stages"] == ["evaluate_pretrain", "evaluate_finetune"]  # pipeline order
    assert d["working_points"] and d["auc"] and d["counts"] and d["caveats"]
    assert {"Animal", "PassengerCar"} <= set(d["classes"])
    logs = c.get(f"/api/v1/runs/{run}/precision-recall/logs").json()
    assert any("ECE" in x["text"] for x in logs["lines"])
    files = c.get(f"/api/v1/runs/{run}/eval-files").json()
    assert files and all(f["purpose"] and f["caveat"] for f in files)
    js = next(f for f in files if f["path"].endswith(".json"))
    pv = c.get(f"/api/v1/runs/{run}/artifacts/preview", params={"path": js["ref"]}).json()
    assert pv["kind"] == "text" and pv["text"].lstrip().startswith(("{", "["))
    assert c.get(f"/api/v1/runs/{run}/artifacts/preview", params={"path": "../../etc/passwd"}).status_code == 404
    pq = next(f for f in files if f["path"].endswith(".parquet"))
    tb = c.get(f"/api/v1/runs/{run}/artifacts/preview", params={"path": pq["ref"]}).json()
    assert tb["kind"] == "table" and tb["columns"] and tb["total_rows"] > 0


def test_inline_only_for_raster_images(container) -> None:
    c = TestClient(create_app(container))
    run = "local:xf-pipe-finetune"
    files = c.get(f"/api/v1/runs/{run}/eval-files").json()
    js = next(f for f in files if f["path"].endswith(".json"))
    r = c.get(f"/api/v1/runs/{run}/artifacts/file", params={"path": js["ref"], "inline": "true"})
    assert (
        r.headers["content-disposition"].startswith("attachment") and "sandbox" in r.headers["content-security-policy"]
    )
