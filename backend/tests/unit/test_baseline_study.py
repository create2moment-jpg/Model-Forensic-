"""Baseline study (Part 1 of the study book), comparison chapters (Part 2), conclusions (Part 3)."""

from __future__ import annotations

import json
import math

import numpy as np

from model_forensic.analysis import baseline_study as bs
from model_forensic.application.knowledge import _datasets
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.reports import svgplot

N = 400
STEPS = np.arange(N, dtype=float) * 10


def ser(key: str, values: np.ndarray) -> tuple[MetricKey, MetricSeries]:
    k = MetricKey.parse(key)
    return k, MetricSeries(key=k, steps=tuple(STEPS), values=tuple(float(v) for v in values))


def run_series() -> dict[MetricKey, MetricSeries]:
    t = np.linspace(0, 1, N)
    out = dict(
        [
            # box: still falling at the end; cls: drops early then flat; radar: σ mutes it, raw barely moves
            ser("train/uloss/box[layer=0]", 10 * (1 - 0.6 * t)),
            ser("train/uloss/cls[layer=0]", 4 + 4 * np.exp(-t * 30)),
            ser("train/uloss/radar_head_class_loss", 8 - 0.5 * t),
            ser("train/task_log_sigma[layer=0,term=box]", np.zeros(N)),
            ser("train/task_log_sigma[layer=0,term=cls]", -0.05 * t),
            ser("train/task_log_sigma[term=radar_head_class_loss]", 3.0 * t),
            ser("train/uloss", 22 - 6 * t),
        ]
    )
    sig = {"box[layer=0]": np.zeros(N), "cls[layer=0]": -0.05 * t, "radar_head_class_loss": 3.0 * t}
    raw = {
        "box[layer=0]": 10 * (1 - 0.6 * t),
        "cls[layer=0]": 4 + 4 * np.exp(-t * 30),
        "radar_head_class_loss": 8 - 0.5 * t,
    }
    for term, s in sig.items():
        out.update([ser(f"train/loss_noreg/{term}", np.exp(-2 * s) * raw[term])])
    return out


def test_loss_progress_reads_raw_loss_per_family() -> None:
    p = {x.family: x for x in bs.loss_progress(run_series())}
    assert p["decoder_bbox"].verdict == "still learning"
    assert p["decoder_cls"].verdict == "plateaued" and p["decoder_cls"].t90 is not None and p["decoder_cls"].t90 < 0.2
    assert p["radar"].change > -0.1  # the raw loss barely moved
    assert len(p["decoder_bbox"].curve) == bs.BINS
    assert set(bs.total_progress(run_series())) == {"uloss"}


def test_uncertainty_weights_find_muted_terms() -> None:
    w = {x.term: x for x in bs.uncertainty_weights(run_series())}
    radar = w["radar_head_class_loss"]
    assert radar.verdict == "self-muted" and radar.trend == "losing trust" and radar.w_end < 0.01
    assert w["box[layer=0]"].verdict == "neutral" and w["box[layer=0]"].trend == "stable"
    assert radar.grad_share is not None and radar.grad_share < 0.01
    assert math.isclose(sum(x.grad_share or 0 for x in w.values()), 1.0, rel_tol=1e-6)
    assert set(bs.family_weight_curves(list(w.values()))) == {"decoder_bbox", "decoder_cls", "radar"}


def test_config_vs_behaviour_flags_mismatches() -> None:
    s = run_series()
    w = bs.uncertainty_weights(s)
    cfg = {
        "loss.uncertainty.enabled": False,
        "loss.uncertainty_weighting": True,
        "loss.task_weights.box": 1,
        "loss.task_weights.cls": 1,
        "loss.task_weights.vel": 1,
        "schedule.max_steps": 1000,
    }
    r = {x.topic: x for x in bs.config_vs_behaviour(cfg, s, w)}
    assert r["Uncertainty weighting"].verdict == "mismatch" and "disagree" in r["Uncertainty weighting"].detail
    assert r["Schedule length"].verdict == "mismatch" and "ran 4.0× the configured steps" in r["Schedule length"].detail
    assert r["Task weight · vel"].verdict == "mismatch"  # configured, but no vel term reaches the gradient
    assert "Learning rate" not in r  # no LR series given → no claim


def test_effective_config_prefers_composed_training_stage() -> None:
    flat = {
        "train_finetune/config.yaml.optimizer.lr": 0.1,
        "train_finetune/stage_output/summaries/train/composed_config.yaml.optimizer.lr": 0.2,
        "evaluate_finetune/config.yaml.optimizer.lr": 0.9,
        "train_finetune/config.yaml.seed": 1,
    }
    cfg = bs.effective_config(flat, ["train_finetune", "evaluate_finetune"])
    assert cfg["optimizer.lr"] == 0.2 and cfg["seed"] == 1
    assert bs.effective_config({"model.name": "x", "a.yaml.model.name": "y"}) == {"model.name": "y"}
    dis = bs.sources_disagree(flat, ["train_finetune", "evaluate_finetune"])
    assert dis and dis[0][0] == "train_finetune/optimizer.lr"


def test_disabled_local_dataset_is_not_a_dataset() -> None:
    flat = {
        "loomy.dataset_name": "online_v3",
        "loomy.local_dataset_name": "gt_local",
        "loomy.use_local_dataset": False,
    }
    assert _datasets(flat) == ["online_v3"]
    flat["loomy.use_local_dataset"] = True
    assert "gt_local" in _datasets(flat)


def test_wide_legend_wraps_below_the_chart() -> None:
    names = [f"family number {i}" for i in range(8)]
    out = svgplot.line_chart([(n, [0, 1], [1, 2]) for n in names], "t", "x", "y")
    assert out.startswith("<svg") and 'class="chart-legend"' in out and "family number 7" in out
    assert 'class="chart-legend"' not in svgplot.line_chart([("a", [0, 1], [1, 2])], "t", "x", "y")


def test_book_parts_with_knowledge(container) -> None:
    import shutil
    from pathlib import Path

    kdir = container.settings.knowledge_dir
    kdir.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[3]
    shutil.copy(root / "config/knowledge/knowledge.example.yaml", kdir / "team.local.yaml")
    st = container.studies.create("GT vs online", runs=["local:xf-pipe-finetune"], baseline="local:xf-online-baseline")
    html_path, summary_path = container.study_book(st.id)
    html = html_path.read_text()
    s = json.loads(summary_path.read_text())
    parts = list(dict.fromkeys(s["parts"].values()))
    assert parts == [
        "Preface",
        "Part 1 · The baseline",
        "Part 2 · Each run against the baseline",
        "Part 3 · Conclusions",
        "Appendix · Forensics across runs",
    ]
    for title in (
        "The baseline — xf-online-baseline",
        "How each loss progressed over training",
        "Uncertainty weighting — which losses σ trusts and which it mutes",
        "The composed configuration against what the run did",
        "Conclusions",
    ):
        assert title in s["chapters"]
    assert s["chapters"].index("Conclusions") < s["chapters"].index("Next steps")
    assert any(t.endswith("against the baseline") for t in s["chapters"])
    # knowledge: the GT finetune (manual labels) sets velocity aside; the online baseline does not
    runs = {r["role"]: r for r in s["runs"]}
    assert "K-manual-labels-no-velocity" in runs["candidate"]["knowledge"]
    assert "K-manual-labels-no-velocity" not in runs["baseline"]["knowledge"]
    assert "set aside · K-animal-deprioritized" in html and "set aside · K-manual-labels-no-velocity" in html
    assert "only for" in html  # conclusions say which run a set-aside applies to
    assert "self-muted" in html and "trust is not share" in html
    assert "Task weight · vel" in html and "Where and why the curves diverge" in html
