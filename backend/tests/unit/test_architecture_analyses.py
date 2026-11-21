"""Architecture analyses over instrumentation-kit metrics."""

import numpy as np

from model_forensic.analysis.base import AnalysisContext, RunData
from model_forensic.analysis.plugins.architecture import (
    DECODER_LAYERS,
    FEATURE_HEALTH,
    MODALITY_ABLATION,
    MODULE_HEALTH,
    DecoderLayersParams,
    FeatureHealthParams,
    ModalityAblationParams,
    ModuleHealthParams,
)
from model_forensic.domain.findings import Severity
from model_forensic.domain.metrics import MetricKey, MetricSeries
from model_forensic.domain.run import Run, RunRef, RunStatus

STEPS = np.arange(0, 6000, 200)


def s(key: str, values) -> MetricSeries:
    values = np.broadcast_to(np.asarray(values, dtype=float), STEPS.shape)
    return MetricSeries(key=MetricKey.parse(key), steps=tuple(map(float, STEPS)), values=tuple(map(float, values)))


def data(*series: MetricSeries, rid: str = "r") -> RunData:
    return RunData(
        run=Run(ref=RunRef(backend="t", run_id=rid), name=rid, status=RunStatus.COMPLETED),
        series={x.key: x for x in series},
    )


def updates(**per_module: float) -> list[MetricSeries]:
    decay = np.linspace(1, 0.2, STEPS.size)
    return [s(f"train/update_ratio[module={m}]", v * decay) for m, v in per_module.items()] + [
        s(f"train/grad_norm[module={m}]", 1.0) for m in per_module
    ]


def test_module_health_starved_and_still() -> None:
    res = MODULE_HEALTH.run(
        AnalysisContext(target=data(*updates(camera=3e-3, lidar=2e-3, fusion=2.5e-3, radar=4e-5))), ModuleHealthParams()
    )
    (f,) = res.findings
    assert f.title.startswith("radar learns") and f.severity == Severity.HIGH and f.recommendations
    res = MODULE_HEALTH.run(AnalysisContext(target=data(*updates(camera=3e-3, radar=1e-9))), ModuleHealthParams())
    assert res.findings[0].title == "radar barely changes during training"


def test_module_health_dead_branch() -> None:
    series = [
        *updates(camera=3e-3, radar=0.0),
        s("train/grad_dead_frac[module=radar]", 1.0),
        s("train/grad_norm[module=radar]", 0.0),
    ]
    res = MODULE_HEALTH.run(AnalysisContext(target=data(*series)), ModuleHealthParams())
    assert res.findings[0].title == "radar receives no gradient"


def test_module_health_healthy_and_skipped() -> None:
    assert not MODULE_HEALTH.run(
        AnalysisContext(target=data(*updates(camera=3e-3, lidar=2e-3, radar=1.5e-3))), ModuleHealthParams()
    ).findings
    assert (
        "update_ratio"
        in MODULE_HEALTH.run(AnalysisContext(target=data(s("train/loss", 1.0))), ModuleHealthParams()).skipped_reason
    )


def layer_losses(factors: list[float], term: str = "cls") -> list[MetricSeries]:
    base = np.linspace(2, 1, STEPS.size)
    return [s(f"train/loss/{term}[layer={k}]", base * f) for k, f in enumerate(factors)]


def test_decoder_saturation_single_finding_for_all_terms() -> None:
    sat = [1.6, 1.25, 1.08, 1.02, 1.015, 1.012]
    res = DECODER_LAYERS.run(
        AnalysisContext(target=data(*layer_losses(sat), *layer_losses(sat, "bbox"))), DecoderLayersParams()
    )
    (f,) = res.findings
    assert (f.title == "Decoder saturates after layer 3" and "cls, bbox" in f.statement) or "bbox, cls" in f.statement
    assert f.recommendations[0].title == "Try 4 decoder layers"


def test_decoder_later_layers_worse_and_still_improving() -> None:
    worse = DECODER_LAYERS.run(AnalysisContext(target=data(*layer_losses([1.6, 1.2, 1.0, 1.1]))), DecoderLayersParams())
    assert worse.findings[0].title.startswith("Later decoder layers make cls worse")
    improving = DECODER_LAYERS.run(
        AnalysisContext(target=data(*layer_losses([2.0, 1.6, 1.3, 1.1]))), DecoderLayersParams()
    )
    assert improving.findings[0].title.startswith("Last decoder layer still improves")


def ablations(nds: dict[str, float], far: dict[str, float]) -> list[MetricSeries]:
    out = [s(f"val/NDS[ablation={a}]", v) for a, v in nds.items()]
    out += [s(f"val/recall[ablation={a},range=60-inf]", v) for a, v in far.items()]
    out += [s(f"val/recall[ablation={a},range=0-30]", 0.78) for a in far]
    return out


def test_modality_contributions() -> None:
    ctx = AnalysisContext(
        target=data(
            *ablations(
                {"none": 0.62, "no_lidar": 0.36, "no_radar": 0.619}, {"none": 0.41, "no_lidar": 0.2, "no_radar": 0.409}
            )
        )
    )
    res = MODALITY_ABLATION.run(ctx, ModalityAblationParams())
    by = {f.title.split()[0]: f for f in res.findings}
    assert by["radar"].title.startswith("radar contributes almost nothing") and by["radar"].severity == Severity.HIGH
    assert by["lidar"].title == "lidar contribution: 41.9% of NDS"
    assert "recall at 60-inf" in by["lidar"].statement


def test_modality_harmful_and_baseline_delta() -> None:
    cand = data(*ablations({"none": 0.60, "no_radar": 0.615}, {"none": 0.40, "no_radar": 0.41}), rid="cand")
    base = data(*ablations({"none": 0.62, "no_radar": 0.60}, {"none": 0.41, "no_radar": 0.37}), rid="base")
    res = MODALITY_ABLATION.run(AnalysisContext(target=cand, baseline=base), ModalityAblationParams())
    titles = [f.title for f in res.findings]
    assert "The model is better without radar" in titles and "radar contribution shrank vs baseline" in titles


def feats(module: str, rms: list[float], zero: list[float]) -> list[MetricSeries]:
    out = []
    for i, (r, z) in enumerate(zip(rms, zero, strict=True)):
        out += [
            s(f"train/feat_rms[module={module},scale={i}]", r),
            s(f"train/feat_std[module={module},scale={i}]", r * 0.8),
            s(f"train/feat_zero_frac[module={module},scale={i}]", z),
        ]
    return out


def test_feature_collapse_not_double_counted_as_imbalance() -> None:
    res = FEATURE_HEALTH.run(
        AnalysisContext(target=data(*feats("radar_fpn", [0.6, 0.5, 1e-6], [0.4, 0.45, 0.99]))), FeatureHealthParams()
    )
    assert [f.title for f in res.findings] == ["radar_fpn scale 2 has collapsed"]


def test_feature_scale_imbalance_and_growth() -> None:
    res = FEATURE_HEALTH.run(
        AnalysisContext(target=data(*feats("radar_fpn", [5.0, 0.5, 0.05], [0.3, 0.3, 0.3]))), FeatureHealthParams()
    )
    assert any(f.title.startswith("radar_fpn scales differ 100×") for f in res.findings)
    grow = data(s("train/feat_rms[module=bev]", np.geomspace(0.1, 10, STEPS.size)))
    assert (
        FEATURE_HEALTH.run(AnalysisContext(target=grow), FeatureHealthParams()).findings[0].title
        == "Activations in bev keep growing"
    )


def test_mlflow_encoded_names_feed_the_same_analyses() -> None:
    series = [
        MetricSeries(
            key=MetricKey.parse(k.replace("[", "__").replace("]", "").replace("=", "_").replace(",", "__")),
            steps=x.steps,
            values=x.values,
        )
        for x in updates(camera=3e-3, radar=4e-5)
        for k in [x.key.to_mlflow()]
    ]
    assert (
        MODULE_HEALTH.run(AnalysisContext(target=data(*series)), ModuleHealthParams())
        .findings[0]
        .title.startswith("radar learns")
    )
