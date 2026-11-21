"""Pipelines: stage metrics merged into the pipeline run; stage_delta compares evaluation stages."""

from model_forensic.analysis.base import AnalysisContext
from model_forensic.analysis.plugins.pipeline import STAGE_DELTA, StageDeltaParams
from model_forensic.application.stages import merge_stage_series
from model_forensic.domain.metrics import MetricKey, MetricSeries
from tests.unit.test_architecture_analyses import data


def ser(key: str, v: float) -> MetricSeries:
    return MetricSeries(key=MetricKey.parse(key), steps=(1.0, 2.0), values=(v, v))


def test_merge_single_and_repeated_keys() -> None:
    merged = {
        str(s.key): s
        for s in merge_stage_series(
            [ser("val/own", 1.0)],
            [
                ("train", [ser("train/loss", 0.5)]),
                ("eval_pre", [ser("val/score[class=Car]", 0.6)]),
                ("eval_ft", [ser("val/score[class=Car]", 0.7)]),
            ],
        )
    }
    assert merged["train/loss"].provenance["stage"] == "train" and "val/own" in merged
    assert merged["val/score[class=Car]"].values[-1] == 0.7  # last stage is the headline
    assert merged["val/score[class=Car,stage=eval_pre]"].values[-1] == 0.6
    assert merged["val/score[class=Car,stage=eval_ft]"].provenance["stage_index"] == "2"


def test_stage_delta_finds_regressions_and_respects_deprioritized() -> None:
    series = merge_stage_series(
        [],
        [
            (
                "evaluate_pretrain",
                [
                    ser("val/score[class=Car]", 0.60),
                    ser("val/score[class=Animal]", 0.20),
                    ser("val/match_err[class=Car]", 0.40),
                    ser("val/score[class=Ped]", 0.50),
                ],
            ),
            (
                "evaluate_finetune",
                [
                    ser("val/score[class=Car]", 0.66),
                    ser("val/score[class=Animal]", 0.10),
                    ser("val/match_err[class=Car]", 0.50),
                    ser("val/score[class=Ped]", 0.505),
                ],
            ),
        ],
    )
    target = data(*series)
    res = STAGE_DELTA.run(AnalysisContext(target=target), StageDeltaParams())
    titles = [f.title for f in res.findings]
    assert titles[0] == "2 metric(s) got worse from evaluate_pretrain to evaluate_finetune"
    assert "1 metric(s) improved from evaluate_pretrain to evaluate_finetune" in titles
    res = STAGE_DELTA.run(AnalysisContext(target=target), StageDeltaParams(deprioritized=("animal",)))
    assert res.findings[0].title.startswith("1 metric(s) got worse") and any("Animal" in n for n in res.notes)
    assert STAGE_DELTA.run(AnalysisContext(target=data(ser("val/x", 1.0))), StageDeltaParams()).skipped_reason


def test_demo_pipeline_end_to_end(container) -> None:
    svc = container.service
    stages = svc.stages("local:xf-pipe-finetune")
    assert [n for n, _ in stages] == ["train_finetune", "evaluate_pretrain", "evaluate_finetune"]
    keys = {str(s.key) for s in svc.series("local:xf-pipe-finetune")}
    assert "train/uloss[task=sparse_detection]" not in keys  # totals stay plain
    assert "train/uloss" in keys and "train/task_log_sigma[task=sparse_detection,term=vel]" in keys
    assert "val/score[class=Pedestrian,stage=evaluate_pretrain,task=sparse_detection]" in keys
    assert any(c.source.startswith("train_finetune/") for c in svc.configs("local:xf-pipe-finetune"))
    res = {r.analysis: r for r in svc.analyze("local:xf-pipe-finetune", analyses=["stage_delta", "task_weighting"])}
    assert res["stage_delta"].findings and res["task_weighting"].skipped_reason is None
