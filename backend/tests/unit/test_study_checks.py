"""Study checks P1–P8, the loss ledger and failure localisation on the demo xflow pipeline vs the online baseline."""

from model_forensic.analysis import evaljson
from model_forensic.analysis import study_checks as sc
from model_forensic.shared import yamlsafe


def test_tagged_yaml_and_ruler_identity() -> None:
    doc = yamlsafe.load(
        "a:\n  combined_metrics_yaml: !!python/object/apply:pathlib.PosixPath\n  - /\n  - opt\n"
        "  - configs\n  - combined_metrics\n  - offline\n  - x.yml\n  t: !!python/tuple [1, 2]\n"
    )
    assert doc["a"]["combined_metrics_yaml"] == "/opt/configs/combined_metrics/offline/x.yml"
    assert sc.ruler_identity(doc["a"]["combined_metrics_yaml"]) == "combined_metrics/offline/x.yml"
    r = sc.check_ruler(
        {
            "a": {"t.combined_metrics_yaml": "/p/combined_metrics/offline/x.yml"},
            "b": {"t.combined_metrics_yaml": "/q/combined_metrics/offline/y.yml"},
        }
    )
    assert r.status == "FAIL" and "RULER MISMATCH" in r.detail


def test_evaljson_parse_and_gt_degenerate() -> None:
    ev = evaljson.EvalEvidence(stage=None, root="r")
    base = "evaluation/alliance_manual_labels/Car"
    evaljson.parse_file(
        f"{base}/torch_metric_evaluation/torch_metric_evaluation.json", {"AP": 0.6, "mAP": float("nan")}, ev
    )
    evaljson.parse_file(
        f"{base}/pr_curves_detailed/pr_curves_detailed__measure=distance.json",
        {"auc": {"0.9000000": {"0.0000000": 0.8, "80.0000000": 0.3}}},
        ev,
    )
    evaljson.parse_file(
        f"{base}/bb3d_match_evaluation/x.json",
        {"velocity_error_stats_binned_by_velocity": [{"size": 990}, {"size": 10}]},
        ev,
    )
    evaljson.parse_file("evaluation/combined_metrics_results.json", {"combined_metric": 0.4}, ev)
    c = ev.classes["Car"]
    assert c.metrics == {"AP": 0.6} and c.auc_by_distance["0.9"] == {0.0: 0.8, 80.0: 0.3}
    assert evaljson.gt_degenerate(c) == {"velocity": 0.99} and ev.combined == {"combined_metric": 0.4}


def test_checks_ledger_and_localization_on_demo(container) -> None:
    res = {
        r.analysis: r
        for r in container.service.analyze(
            "local:xf-pipe-finetune",
            "local:xf-online-baseline",
            analyses=["study_checks", "loss_ledger", "eval_localization"],
        )
    }
    status = {n.split(" ")[0]: n.split(" ")[1] for n in res["study_checks"].notes}
    assert status == {
        "P1": "FAIL",
        "P2": "INFO",
        "P3": "FAIL",
        "P4": "FAIL",
        "P5": "FAIL",
        "P6": "PASS",
        "P7": "WARN",
        "P8": "WARN",
    }, res["study_checks"].notes
    assert "converged early" in next(n for n in res["study_checks"].notes if n.startswith("P8"))
    ledger_titles = [f.title for f in res["loss_ledger"].findings]
    assert "Self-muted: Radar head" in ledger_titles
    assert any("σ tax" in n for n in res["loss_ledger"].notes)
    loc = [f.title for f in res["eval_localization"].findings]
    assert any(t.startswith("Far-range collapse") for t in loc)
    assert any(t.startswith("Shared ruler: better than the baseline") for t in loc)


def test_plateau_rules() -> None:
    import numpy as np

    from model_forensic.domain.metrics import MetricKey, MetricSeries

    steps = np.arange(100.0)

    def s(name: str, v) -> MetricSeries:
        return MetricSeries(key=MetricKey.parse(name), steps=tuple(steps), values=tuple(map(float, v)))

    lr_high = s("train/lr", np.linspace(1e-3, 8e-4, 100))
    lr_dead = s("train/lr", np.linspace(1e-3, 1e-6, 100) ** 1)
    learning = s("train/loss", 2 - steps / 100)
    early = s("train/loss", 1 + np.exp(-steps / 5))
    assert sc.plateau(learning, lr_high).detail.split("; ")[1].startswith("learning")
    assert "converged early" in sc.plateau(early, lr_high).detail
    late = s("train/loss", 1 + np.exp(-steps / 25))
    assert "schedule-bound" in sc.plateau(late, s("train/lr", 1e-3 * (1 - steps / 100) ** 6)).detail
    assert sc.plateau(None, None).status == "SKIP"
    _ = lr_dead
