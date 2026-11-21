"""Every built-in template must load, render without leftovers, and declare valid evidence."""

import re

import pytest

from model_forensic.ai.prompts import KNOWN_VARIABLES, EvidenceSpec, PromptLibrary, PromptTemplate
from model_forensic.shared.errors import ValidationError

LIB = PromptLibrary.load()
ALL = LIB.all()
FULL_VARS = {v: f"<{v}>" for v in KNOWN_VARIABLES}


def test_pack_size_and_categories() -> None:
    assert len(ALL) >= 80
    cats = {p.category for p in ALL}
    assert {"Camera branch", "Radar branch", "LiDAR & fusion", "Compare experiments", "Logs & infrastructure"} <= cats


@pytest.mark.parametrize("p", ALL, ids=lambda p: p.id)
def test_template_renders_cleanly(p: PromptTemplate) -> None:
    auto = {"run_id": "<run>", "run_ids": "<runs>"}  # filled by the investigator
    for variables in ({**auto, **{k: FULL_VARS[k] for k in p.requires}}, FULL_VARS):
        text = p.render(variables)
        assert "{{" not in text and "}}" not in text, text
        assert "(unspecified)" not in text or not set(p.requires) - {"run_id", "baseline_run_id"}
    assert p.title and p.description, "picker needs a title and a description"
    assert "run_id" in p.requires or "hypothesis" in p.requires


def test_optional_and_inverted_sections() -> None:
    t = LIB.get("cmp_camera_backbone")
    assert "stronger camera backbone (ConvNeXt-B)" in t.render(
        {"run_id": "a", "baseline_run_id": "b", "backbone": "ConvNeXt-B"}
    )
    plain = t.render({"run_id": "a", "baseline_run_id": "b"})
    assert "different camera backbone" in plain and "Change details" not in plain


def test_comparison_prompts_are_flagged() -> None:
    assert LIB.get("cmp_radar_multiscale").needs_baseline
    assert not LIB.get("cmp_multi_rank").needs_baseline  # multi-run prompts use run_ids instead


def test_pack_default_include_is_merged() -> None:
    radar = LIB.get("cmp_radar_multiscale").include
    assert "*radar*" in radar.config_keys and radar.slices
    nan = LIB.get("logs_nan").include
    assert nan.logs == "errors+tail" and "train/grad_norm" in nan.series


def test_invalid_include_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceSpec.parse({"logs": "everything"}, "x")
    with pytest.raises(ValidationError):
        EvidenceSpec.parse({"bogus": 1}, "x")


def test_no_duplicate_titles() -> None:
    titles = [p.title.lower() for p in ALL]
    assert len(titles) == len(set(titles))


def test_ids_are_snake_case() -> None:
    assert all(re.fullmatch(r"[a-z][a-z0-9_]+", p.id) for p in ALL)
