"""Study wizard: templates, pre-registration, checks-first gate, decision record (export + book + API)."""

import pytest
from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.application.study_templates import TEMPLATES
from model_forensic.shared.errors import ValidationError


def wizard(container, template="change_better", **kw):
    return container.studies.create_from_template(
        template,
        kw.pop("title", "Is the finetune better?"),
        kw.pop("runs", ["local:xf-pipe-finetune"]),
        kw.pop("baseline", "local:xf-online-baseline"),
        **kw,
    )


def test_templates_are_complete() -> None:
    assert {t.id for t in TEMPLATES} == {
        "change_better",
        "why_regressed",
        "train_longer",
        "head_useful",
        "pretrain_finetune",
    }
    for t in TEMPLATES:
        assert t.claim and t.falsified_if and t.confounders and "study_checks" in t.analyses


def test_preregistration_and_hypothesis(container) -> None:
    s = wizard(container, deciding_metric="val/score[class=Pedestrian]")
    pr = s.preregistration
    assert pr is not None and pr.template == "change_better"
    assert "local:xf-pipe-finetune" in pr.claim and pr.deciding_metric == "val/score[class=Pedestrian]"
    assert pr.ruler == "combined_metrics/offline/box3d_sequential.yml"  # baseline's ruler recorded
    assert "{" not in s.goal and "local:xf-online-baseline" in s.goal
    assert s.hypotheses[0].source == "preregistered" and s.hypotheses[0].statement == pr.claim
    s = container.studies.check_confounder(s.id, pr.confounders[0])
    assert s.preregistration.confounders_checked == (pr.confounders[0],)
    with pytest.raises(ValidationError):
        container.studies.check_confounder(s.id, "not a confounder")
    with pytest.raises(ValidationError):
        container.studies.create_from_template("change_better", "x", ["local:xf-pipe-finetune"])  # needs baseline
    with pytest.raises(ValidationError):
        container.studies.create_from_template("nope", "x", ["local:xf-pipe-finetune"])
    s2 = container.studies.create_from_template("train_longer", "", ["local:xf-pipe-finetune"])
    assert s2.title == "Should I train longer?" and s2.preregistration.deciding_metric == "train/loss"


def test_gate_blocks_verdict_until_override(container) -> None:
    s = wizard(container)
    s = container.studies.run_gate(s.id)
    assert s.gate is not None and not s.gate.open
    assert any(b.startswith("P1") for b in s.gate.blocking) and any(b.startswith("P3") for b in s.gate.blocking)
    with pytest.raises(ValidationError) as exc:
        container.studies.decide(s.id, "adopt", "Ship the finetune")
    assert "P1" in str(exc.value)
    s = container.studies.decide(s.id, "more_evidence", "Re-score both runs on one ruler first")
    assert s.decision.outcome.value == "more_evidence"
    s = container.studies.decide(
        s.id,
        "adopt",
        "Ship the finetune",
        rationale="Shared-ruler table: ahead in 25/40",
        gate_override="Compared on ruler-free per-class metrics only",
        based_on=["hy-1"],
    )
    assert s.decision.gate_override and s.decision.based_on == ("hy-1",)
    md = container.studies.to_markdown(s.id)
    for needle in (
        "## Decision",
        "**ADOPT** — Ship the finetune",
        "## Pre-registration",
        "## Checks (P1–P8)",
        "blocked by",
    ):
        assert needle in md
    html_path, _ = container.study_book(s.id)
    html = html_path.read_text()
    assert "Pre-registration" in html and "Gate blocked" in html and "ADOPT" in html
    with pytest.raises(ValidationError):
        container.studies.decide(s.id, "maybe", "x")


def test_wizard_api(container) -> None:
    c = TestClient(create_app(container))
    assert len(c.get("/api/v1/study-templates").json()) == 5
    st = c.post(
        "/api/v1/studies/wizard", json={"template": "pretrain_finetune", "runs": ["local:xf-pipe-finetune"]}
    ).json()
    sid = st["id"]
    assert st["preregistration"]["template"] == "pretrain_finetune"
    g = c.post(f"/api/v1/studies/{sid}/gate").json()["gate"]
    assert g["checks"][0]["id"] == "P1" and g["blocking"]
    r = c.put(f"/api/v1/studies/{sid}/decision", json={"outcome": "reject", "decision": "No"})
    assert r.status_code == 400 and "blocking" in r.text
    ok = c.put(f"/api/v1/studies/{sid}/decision", json={"outcome": "inconclusive", "decision": "Need GT velocity"})
    assert ok.status_code == 200 and ok.json()["decision"]["outcome"] == "inconclusive"
    conf = st["preregistration"]["confounders"][0]
    assert c.put(f"/api/v1/studies/{sid}/confounders", json={"confounder": conf}).json()["preregistration"][
        "confounders_checked"
    ] == [conf]
    assert c.post("/api/v1/studies/wizard", json={"template": "x", "runs": ["local:a"]}).status_code == 400
