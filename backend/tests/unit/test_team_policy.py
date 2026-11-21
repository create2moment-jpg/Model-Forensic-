"""Team policy (config/team.local.yaml): de-prioritised classes, expected metrics, code refs, analysis defaults."""

from pathlib import Path

import pytest

from model_forensic.analysis.base import AnalysisContext
from model_forensic.analysis.plugins.pipeline import METRIC_COVERAGE, CoverageParams
from model_forensic.application.stages import merge_stage_series
from model_forensic.interface import team
from tests.unit.test_pipeline_stages import ser


@pytest.fixture()
def policy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    f = tmp_path / "team.local.yaml"
    monkeypatch.setenv("MF_TEAM_CONFIG", str(f))
    return f


def test_load_template_and_lists(policy_file: Path) -> None:
    policy_file.write_text(Path(__file__).parents[3].joinpath("config/team.example.yaml").read_text())
    pol, notice = team.load()
    assert notice is None and pol.is_deprioritized("animal")
    assert pol.expected_metrics["training"]["train/uloss"] == "unweighted total loss"
    assert pol.code_ref(
        "train/task_log_sigma[term=vel]",
    ).endswith("uncertainty_weighted_task_loss.py")
    policy_file.write_text("deprioritized_classes: Animal\nexpected_metrics: {evaluation: ['val/score*']}\n")
    pol, _ = team.load()
    assert pol.deprioritized_classes == ["Animal"] and pol.expected_metrics == {
        "evaluation": {"val/score*": "val/score*"}
    }
    policy_file.write_text("deprioritized_classes: [\n")
    pol, notice = team.load()
    assert notice and pol.deprioritized_classes == []


def test_metric_coverage_per_stage() -> None:
    series = merge_stage_series([], [("train", [ser("train/loss", 1.0)]), ("eval", [ser("val/score[class=Car]", 0.5)])])
    from tests.unit.test_architecture_analyses import data

    p = CoverageParams(
        expected={
            "training": {"train/loss": "total", "train/uloss": "unweighted"},
            "evaluation": {"val/score*": "score", "val/pr_auc*": "pr auc"},
        }
    )
    res = METRIC_COVERAGE.run(AnalysisContext(target=data(*series)), p)
    (f,) = res.findings
    assert f.title == "Expected metrics not logged (2 families)"
    assert {e.statement for e in f.evidence} == {
        "train (training stage) logged no: unweighted",
        "eval (evaluation stage) logged no: pr auc",
    }


def test_policy_applied_to_analyses_and_ai(container, policy_file: Path) -> None:
    policy_file.write_text(
        "deprioritized_classes: [Pedestrian]\ncode_refs: {'train/task_log_sigma*': 'xflow/losses/uw.py'}\n"
        "analysis_params: {sequence_drilldown: {fp_per_gt: 5.0}}\n"
    )
    svc = container.service
    svc.team, _ = team.load()
    res = {
        r.analysis: r
        for r in svc.analyze(
            "local:xf-pipe-finetune", analyses=["stage_delta", "sequence_drilldown", "metric_coverage"]
        )
    }
    assert not any("Pedestrian" in f.title for f in res["sequence_drilldown"].findings)
    assert not any("Precision-poor" in f.title for f in res["sequence_drilldown"].findings)
    assert all(
        "Pedestrian" not in e.statement
        for f in res["stage_delta"].findings
        if "worse" in f.title
        for e in f.evidence[1:]
    )
    cb, _ = container.investigator().build_context("local:xf-pipe-finetune", None)
    text = "\n".join(i.text for _, i in cb._items)
    assert "de-prioritised classes" in text and "xflow/losses/uw.py" in text
