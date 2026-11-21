"""The study book reproduces the conclusions of the hand-written 1V5R study (docs/study-reproduction.md)."""

import json
import math

from model_forensic.analysis import study_checks as sc
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.reports import study_book as sb


def ser(key: str, values, steps=None, stage_index: int | None = None) -> MetricSeries:
    steps = steps or list(range(len(values)))
    prov = {"stage_index": str(stage_index)} if stage_index is not None else {}
    return MetricSeries(
        key=MetricKey.parse(key), steps=tuple(float(s) for s in steps), values=tuple(values), provenance=prov
    )


def test_plateau_in_epochs_with_wasted_epochs() -> None:
    n = 400
    loss = ser("train/loss", [2.0 * math.exp(-i / 15) + 0.3 for i in range(n)])
    lr = ser("train/lr", [8.7e-4] * n)
    r = sc.plateau(loss, lr, epochs=8)
    assert r.status == "WARN" and "converged early" in r.detail
    assert r.data["epochs"] == 8 and r.data["plateau_epoch"] < 2 and r.data["epochs_after_plateau"] > 6
    assert "epoch" in r.detail and "of 8 epochs after the plateau" in r.detail
    epoch = ser("train/epoch", [i / 50 for i in range(n)])  # 8 epochs logged
    r2 = sc.plateau(loss, lr, epoch=epoch)
    assert r2.data["epochs"] == 8 and abs(r2.data["plateau_epoch"] - r.data["plateau_epoch"]) < 0.5


def test_max_epochs_from_config() -> None:
    assert sc.max_epochs({"trainer.max_epochs": 8, "data.batch": 4}) == 8
    assert sc.max_epochs({"x.num_epochs": "3"}) == 3
    assert sc.max_epochs({"x.lr": 1}) is None


def _ledger_series(stage: str, idx: int, radar: float, box: float) -> dict:
    out = {}
    for term, raw in (("radar_velocity", radar), ("decoder_bbox", box)):
        for pre, v in (("uloss/", raw), ("loss/", raw * 0.5), ("loss_noreg/", raw * 0.4)):
            s = ser(f"train/{pre}{term}[stage={stage}]", [v] * 20, stage_index=idx)
            out[s.key] = s
        s = ser(f"train/task_log_sigma[term={term},stage={stage}]", [0.5] * 20, stage_index=idx)
        out[s.key] = s
    return out


def test_budget_reallocation_between_training_stages() -> None:
    series = {
        **_ledger_series("pretrain", 0, radar=0.53, box=0.40),
        **_ledger_series("finetune", 1, radar=0.52, box=0.05),
    }
    stages = sc.ledger_by_stage(series)
    assert [s for s, _ in stages] == ["pretrain", "finetune"]
    shift = {x["family"]: x for x in sc.budget_shift(stages[0][1], stages[-1][1])}
    radar = next(v for k, v in shift.items() if "radar" in k)
    assert radar["shift_pts"] > 20  # share rose although its absolute loss barely moved
    assert abs(radar["last"] - radar["first"]) < 0.05 * radar["first"]


def test_metric_families() -> None:
    assert sb.metric_family("ate") == "localization"
    assert sb.metric_family("mota") == "tracking"
    assert sb.metric_family("ave") == "velocity"
    assert sb.metric_family("combined_metric") == "headline"
    assert sb.metric_family("pr_auc") == "detection"


class _Reg:
    def lookup(self, name: str):
        class M:
            higher_is_better = name in {"combined_metric", "mota", "pr_auc"}

        return M()


def test_finetune_paradox_and_velocity_artifact() -> None:
    vals = {"combined_metric": (0.404, 0.403), "ate": (0.335, 0.313), "mota": (0.128, 0.205), "ave": (1.2, 0.6)}
    series = {}
    for name, (a, z) in vals.items():
        for st, idx, v in (("evaluate_pretrain", 0, a), ("evaluate_finetune", 1, z)):
            s = ser(f"val/{name}[stage={st}]", [v], stage_index=idx)
            series[s.key] = s
    r = sb.RunEvidence(
        label="gt-tri",
        run=None,
        role="candidate",
        stages=[],
        series=series,
        evals=[],
        config={},
        lr=None,  # type: ignore[arg-type]
        results={},
        checks=[sc.CheckResult("P3", "GT", "FAIL", "degenerate ground truth: Car: velocity 99% in one bin")],
        ledger=[],
        registry=_Reg(),
    )
    html = "".join(sb.stage_families_html(r))
    assert "looks useless on the headline ruler" in html and "localization and tracking" in html
    assert "velocity change is an artifact" in html and "not trustworthy" in html


def test_demo_book_capacity_verdict(container) -> None:
    st = container.studies.create(
        "GT vs online", goal="g", runs=["local:xf-pipe-finetune"], baseline="local:xf-online-baseline"
    )
    html_path, summary_path = container.study_book(st.id)
    s = json.loads(summary_path.read_text())
    assert "Capacity — are we leaving performance on the table?" in s["chapters"]
    html = html_path.read_text()
    assert "REJECTED (provisionally)" in html and "Overfit one batch" in html
    assert "of 3 epochs after the plateau" in html
