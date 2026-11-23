"""Analysis dossier: every analysis in one document with evidence ids, one stage at a time, saved to the inbox."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from model_forensic.application import dossier as ds

RUN, BASE = "local:cand-radar-8f", "local:baseline-cl4f-s0"


def test_dossier_against_a_baseline(container) -> None:  # type: ignore[no-untyped-def]
    t = ds.build(container.service, container.knowledge, container.docs, RUN, BASE)
    for section in ("## 1. The run", "## 2. Configuration", "## 3. Results", "## 4. Loss trajectories",
                    "## 5. Training health questions", "## 6. Loss drift", "## 7. Against the codebase",
                    "## 12. What could not be examined", "# Instructions for the reader"):  # fmt: skip
        assert section in t, section
    ids = re.findall(r"\[(E-[A-Z]+\d+)\]", t.split("# Instructions for the reader")[0])
    assert len(ids) == len(set(ids)) > 20  # every observation has its own evidence id
    assert "model.fusion.modalities" in t and "radar" in t  # the config difference
    assert "stages are never mixed" in t
    assert "password" not in t.lower() and "secret" not in t.lower()


def test_single_run_stage_and_brief(container) -> None:  # type: ignore[no-untyped-def]
    full = ds.build(container.service, container.knowledge, container.docs, "local:xf-pipe-finetune")
    assert "pipeline stages (order): train_finetune, evaluate_pretrain, evaluate_finetune" in full
    assert "a baseline (comparison, drift)" in full
    ev = ds.build(container.service, container.knowledge, container.docs, "local:xf-pipe-finetune",
                  stage="evaluate_finetune", with_prompt=False)  # fmt: skip
    assert "**evaluate_finetune**" in ev and "training losses (none logged in this stage)" in ev
    assert "# Instructions for the reader" not in ev
    with pytest.raises(ValueError):
        ds.build(container.service, container.knowledge, container.docs, "local:xf-pipe-finetune", stage="nope")
    brief = ds.build(container.service, container.knowledge, container.docs, RUN, BASE, detail="brief")
    assert len(brief) < len(ds.build(container.service, container.knowledge, container.docs, RUN, BASE, detail="full"))


def test_save_api_and_cli(container, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from model_forensic.api.app import create_app
    from model_forensic.cli import main as cli

    c = TestClient(create_app(container))
    r = c.get(f"/api/v1/runs/{RUN}/dossier", params={"baseline": BASE, "save": True})
    assert r.status_code == 200 and r.text.startswith("# Analysis dossier")
    saved = r.headers["X-Dossier-Path"]
    assert "/dossiers/" in saved and Path(saved).read_text(encoding="utf-8") == r.text
    assert c.get(f"/api/v1/runs/{RUN}/dossier", params={"stage": "nope"}).status_code == 400
    monkeypatch.setattr(cli, "_c", lambda _cfg=None: container)
    out = tmp_path / "d.md"
    res = CliRunner().invoke(cli.app, ["dossier", "--run", RUN, "--baseline", BASE, "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert out.read_text().startswith("# Analysis dossier")
