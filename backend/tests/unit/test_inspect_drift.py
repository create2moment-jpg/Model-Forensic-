"""Format drift: unknown or broken files give notes, never crashes or silent zeros; v1 downloads are readable."""

import json
from pathlib import Path

from typer.testing import CliRunner

from model_forensic.analysis import evaljson
from model_forensic.application.inspect import inspect
from model_forensic.cli.main import app
from model_forensic.infrastructure.connectors.naming import canonical_key


def v1_tree(root: Path) -> Path:
    """The v1 tool's outputs/_azure_artefacts layout with a few real-shaped files."""
    m = root / "_azure_metrics" / "run_a"
    m.mkdir(parents=True)
    (m / "metric_histories.json").write_text(
        json.dumps(
            {
                "stages": {
                    "train_torch": {
                        "histories": {
                            "train_sparse_detection/d0_dn_loss_cls/unweighted_loss": [1.0, 0.5],
                            "train_weighted_total_loss": [2.0, 1.0],
                            "some_custom_counter": [1, 2],
                        },
                        "steps": {"train_sparse_detection/d0_dn_loss_cls/unweighted_loss": [0, 10]},
                    }
                }
            }
        )
    )
    c = root / "_azure_configs" / "run_a" / "stage" / "summaries"
    c.mkdir(parents=True)
    (c / "composed_config.yaml").write_text(
        "trainer:\n  best_checkpoint_monitor: val_total_loss_epoch\ntasks:\n  eval:\n"
        "    combined_metrics_yaml: !!python/object/apply:pathlib.PosixPath\n    - /\n    - opt\n    - configs\n"
        "    - combined_metrics\n    - offline\n    - box3d_ground_truth.yml\n"
    )
    e = root / "_azure_eval" / "guid" / "evaluation" / "alliance_manual_labels" / "Car"
    (e / "torch_metric_evaluation").mkdir(parents=True)
    (e / "torch_metric_evaluation" / "torch_metric_evaluation.json").write_text(json.dumps({"AP": 0.6, "mAP": None}))
    (e / "pr_curves_detailed").mkdir()
    (e / "pr_curves_detailed" / "pr_curves_detailed__measure=distance.json").write_text(json.dumps({"area": {}}))
    (e / "false_positives_analysis").mkdir()
    (e / "false_positives_analysis" / "fp.json").write_text("{not json")
    (root / "_azure_eval" / "guid" / "evaluation" / "new_family.json").write_text(json.dumps({"x": 1}))
    (root / "_azure_eval" / "guid" / "evaluation" / "broken.parquet").write_bytes(b"PAR1junk")
    return root


def test_inspect_v1_tree_reports_drift_without_crashing(tmp_path: Path) -> None:
    rep = inspect(v1_tree(tmp_path))
    assert rep.metric_names == 3 and rep.unmapped == ["some_custom_counter"]
    assert (
        rep.mapped_examples["train_sparse_detection/d0_dn_loss_cls/unweighted_loss"]
        == "train/uloss/cls[branch=dn,layer=0,task=sparse_detection]"
    )
    assert rep.configs[0]["ruler"] == "combined_metrics/offline/box3d_ground_truth.yml"
    assert rep.configs[0]["monitor"] == "val_total_loss_epoch"
    assert "torch_metric_evaluation" in rep.eval_classes["Car"]
    notes = "\n".join(rep.notes)
    assert "pr_curves_detailed" in notes and "no values extracted" in notes  # changed key → visible note
    assert "JSONDecodeError" in notes  # corrupt file → note
    assert any(p.endswith("new_family.json") for p in rep.eval_unrecognised)
    assert rep.parquets[0]["kind"] == "unknown" and not rep.ok()


def test_cli_inspect(tmp_path: Path) -> None:
    r = CliRunner().invoke(app, ["inspect", str(v1_tree(tmp_path)), "--json"])
    assert r.exit_code == 1 and json.loads(r.stdout)["metric_names"] == 3


def test_odd_inputs_never_raise() -> None:
    ev = evaljson.EvalEvidence(stage=None, root="r")
    for path, doc in [
        ("a/Car/torch_metric_evaluation/x.json", [1, 2]),
        ("a/torch_metric_evaluation/x.json", {"AP": 1}),
        ("a/Car/pr_curves_detailed/pr_curves_detailed__measure=distance.json", {"auc": {"x": {"y": "z"}}}),
        ("a/Car/false_positives_analysis/f.json", {"worst_sequences": [["s"], 5, ["s", {"x": 1}]]}),
        ("a/Car/bb3d_match_evaluation/b.json", {"x_binned_by_v": [{"size": "n"}, 3]}),
    ]:
        evaljson.parse_file(path, doc, ev)
    for raw in ["evaluation/x/Car.pr_curves_detailed.auc@nan@40", "train_/x", "///", "val/x[a=", "évaluation/ü"]:
        canonical_key(raw)
