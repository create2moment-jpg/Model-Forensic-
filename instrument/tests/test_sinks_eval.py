import json
from types import SimpleNamespace

from model_forensic_instrument import JsonlSink, MlflowSink, nuscenes_breakdown
from model_forensic_instrument.evaluation import evaluate_by_slices, log_slices


def test_mlflow_sink_encodes_and_batches() -> None:
    calls = []
    fake = SimpleNamespace(log_metrics=lambda m, step: calls.append((m, step)))
    sink = MlflowSink(fake)
    sink.log({f"train/grad_norm[module=m{i}]": float(i) for i in range(1000)}, step=5)
    assert len(calls) == 2 and sum(len(m) for m, _ in calls) == 1000
    assert "train/grad_norm__module_m7" in calls[0][0] and calls[0][1] == 5


def test_jsonl_sink_matches_local_connector_format(tmp_path) -> None:
    sink = JsonlSink(tmp_path / "run" / "metrics.jsonl")
    log_slices(sink, 100, "recall", {"0-30": 0.8, "60-inf": float("nan")})
    rows = [json.loads(line) for line in (tmp_path / "run" / "metrics.jsonl").read_text().splitlines()]
    assert {k: rows[0][k] for k in ("key", "step", "value")} == {
        "key": "val/recall[range=0-30]",
        "step": 100,
        "value": 0.8,
    }
    assert rows[1]["value"] == "NaN"


def test_nuscenes_breakdown() -> None:
    summary = {
        "mean_ap": 0.55,
        "nd_score": 0.62,
        "tp_errors": {"trans_err": 0.52, "vel_err": 0.28},
        "mean_dist_aps": {"car": 0.66, "pedestrian": 0.55},
        "label_aps": {"car": {"0.5": 0.4, "4.0": 0.8}},
        "label_tp_errors": {"car": {"trans_err": 0.3, "attr_err": float("nan")}},
    }
    out = nuscenes_breakdown(summary)
    assert out["val/NDS"] == 0.62 and out["val/mATE"] == 0.52 and out["val/mAVE"] == 0.28
    assert out["val/AP[class=car]"] == 0.66 and out["val/AP[class=car,thr=4.0]"] == 0.8
    assert out["val/ATE[class=car]"] == 0.3 and "val/AAE[class=car]" not in out


def test_evaluate_by_slices() -> None:
    gt = [SimpleNamespace(x=10.0, y=0.0), SimpleNamespace(x=70.0, y=0.0)]

    def evaluate(keep):
        return {"recall": sum(1 for b in gt if keep(b)) / len(gt)}

    bins = {"0-30": lambda b: (b.x**2 + b.y**2) ** 0.5 < 30, "60-inf": lambda b: (b.x**2 + b.y**2) ** 0.5 >= 60}
    res = evaluate_by_slices(evaluate, bins)
    assert res == {"0-30": {"recall": 0.5}, "60-inf": {"recall": 0.5}}
