"""Filter facets: defaults, local override, mapping of names, observed values, the brief and the CLI / API."""

from __future__ import annotations

from pathlib import Path

from model_forensic.application import facets as fc

NAMES = [
    "val/AP[cls=car,dist=0-30]",
    "val/AP[cls=ped,dist=30-60]",
    "train/feat_rms[module=camera_fpn,scale=0]",
    "train/loss/rdr_head",
    "train/loss/cls[layer=3]",
    "val/NDS",
]


def test_defaults_map_aliases_and_modality_from_name() -> None:
    cfg = fc.load(local=Path("/nonexistent.yaml"))
    assert cfg.order[:3] == ["class", "modality", "distance"]
    assert fc.facets_of("val/AP[cls=car,dist=0-30]", cfg) == {"class": "car", "distance": "0-30"}
    assert fc.facets_of("train/feat_rms[module=camera_fpn,scale=0]", cfg)["modality"] == "camera"
    assert "modality" not in fc.facets_of("val/mAP", cfg)  # "map" is no modality, and no substring tricks
    assert "modality" not in fc.facets_of("train/loss/rdr_head", cfg)  # unknown spelling without a local file


def test_local_override_replaces_facets_and_hides_dims(tmp_path: Path) -> None:
    local = tmp_path / "facets.local.yaml"
    local.write_text(
        "hide_dims: [scale]\nfacets:\n  class:\n    aliases: [cls]\n    value_aliases: {ped: pedestrian}\n"
        "  modality:\n    values: [camera, radar]\n    value_aliases: {rdr: radar}\n    from_name: true\n"
    )
    cfg = fc.load(local=local)
    assert cfg.sources[-1] == str(local) and cfg.notice is None
    assert fc.facets_of("val/AP[cls=ped]", cfg) == {"class": "pedestrian"}
    assert fc.facets_of("train/loss/rdr_head", cfg)["modality"] == "radar"
    assert "scale" not in fc.facets_of("train/feat_rms[module=camera_fpn,scale=0]", cfg)
    assert "distance" in cfg.facets  # facets not given keep their defaults


def test_broken_local_file_falls_back_with_a_notice(tmp_path: Path) -> None:
    bad = tmp_path / "facets.local.yaml"
    bad.write_text("facets: [not, a, mapping]\n")
    cfg = fc.load(local=bad)
    assert cfg.notice and "not used" in cfg.notice
    assert "class" in cfg.facets
    assert fc.validate(bad) and not fc.validate(fc.DEFAULTS)


def test_observed_and_brief_list_values_and_unclaimed_dims() -> None:
    cfg = fc.load(local=Path("/nonexistent.yaml"))
    obs = fc.observed(NAMES, cfg)
    assert obs["facets"]["class"] == {"car": 1, "ped": 1}
    assert "layer" in obs["unclaimed_dims"] and "cls" not in obs["unclaimed_dims"]
    text = fc.brief(NAMES, cfg, "PROMPT BODY")
    assert "`ped` (1)" in text and "Where to write" in text and text.rstrip().endswith("PROMPT BODY")
    assert "facets.local.yaml" in text


def test_api_and_cli(container, monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from model_forensic.api.app import create_app
    from model_forensic.cli.main import app

    monkeypatch.setenv("MF_FACETS_CONFIG", str(tmp_path / "none.yaml"))
    client = TestClient(create_app(container))
    body = client.get("/api/v1/facets").json()
    assert body["order"][0] == "class" and "modality" in body["facets"]
    res = CliRunner().invoke(app, ["facets", "--check", str(fc.DEFAULTS)])
    assert res.exit_code == 0, res.output
    bad = tmp_path / "bad.yaml"
    bad.write_text("- 1\n")
    assert CliRunner().invoke(app, ["facets", "--check", str(bad)]).exit_code == 1
