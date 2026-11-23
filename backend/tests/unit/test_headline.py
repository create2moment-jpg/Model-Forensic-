"""The Overview headline: combined metrics first, then one metric per class (de-prioritised classes left out)."""

from __future__ import annotations

from model_forensic.application.headline import headline
from model_forensic.interface.team import OverviewPolicy, TeamPolicy

DIM = [
    "train/loss", "val/score[class=combined_metric,task=det]", "val/score[class=Car,task=det]",
    "val/score[class=Animal,task=det]", "val/score[class=Bike,task=det]", "val/ap[class=Car,task=det]",
    "val/score[class=Car,stage=evaluate,task=det]", "val/match_rmse[class=Car,dist=40,task=det]",
]  # fmt: skip
NAMED = [
    "val/combined_metric[task=det]", "val/Car[task=det]", "val/Car[class=alliance,task=det]", "val/Car_DetectionScore[task=det]",
    "val/Truck[task=det]", "val/Truck_AverageRecall[task=det]", "val/Animal[task=det]", "val/Animal_DetectionScore[task=det]",
    "val/Lonely[task=det]",
]  # fmt: skip


def test_class_dimension_auto() -> None:
    h = headline(DIM, OverviewPolicy(), ["Animal"])
    assert h["combined"] == ["val/score[class=combined_metric,task=det]"]
    assert [r["class"] for r in h["per_class"]] == ["Bike", "Car"]  # Animal de-prioritised, combined not a class
    assert h["per_class_metric"] == "val/score[task=det]" and "stage=" not in "".join(h["keys"])
    assert h["keys"][0] == h["combined"][0]


def test_class_in_the_name_auto_and_template() -> None:
    h = headline(NAMED, OverviewPolicy(), ["animal"])
    assert h["combined"] == ["val/combined_metric[task=det]"]
    assert h["per_class"] == [
        {"class": "Car", "key": "val/Car[task=det]"},
        {"class": "Truck", "key": "val/Truck[task=det]"},
    ]
    t = headline(NAMED, OverviewPolicy(per_class_metric="val/{class}_DetectionScore[task=det]"), [])
    assert [r["class"] for r in t["per_class"]] == ["Animal", "Car"]


def test_configured_family_exclusions_and_limits() -> None:
    pol = OverviewPolicy(per_class_metric="val/ap*", exclude_classes=["Car"], combined=["*nothing*"])
    assert headline(DIM, pol, [])["keys"] == []  # no combined match, no plain evaluation metric, Car excluded
    pol = OverviewPolicy(per_class_metric="val/score*", max_classes=1)
    assert len(headline(DIM, pol, [])["per_class"]) == 1
    team = TeamPolicy.model_validate({"overview": {"per_class_metric": "val/{class}[task=det]", "max_classes": 3}})
    assert team.overview.per_class_metric.startswith("val/{class}") and team.overview.combined == ["*combined*"]


def test_overview_carries_the_groups(container) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    container.service.team.deprioritized_classes = ["Animal"]
    container.service.series("local:xf-pipe-finetune")  # the values come with the metric histories
    c = TestClient(create_app(container))
    g = c.get("/api/v1/runs/local:xf-pipe-finetune/overview-cached").json()["headline_groups"]
    assert g and g["combined"] and len(g["per_class"]) >= 3 and all(r["class"] != "Animal" for r in g["per_class"])
    assert g["keys"][: len(g["combined"])] == g["combined"]
    container.service.series("local:cand-radar-8f")
    other = c.get("/api/v1/runs/local:cand-radar-8f/overview-cached").json()["headline_groups"]
    assert other["per_class_metric"] == "val/AP" and other["combined"]  # no combined metric: the plain ones stand in
    assert all("[" not in k for k in other["combined"])
