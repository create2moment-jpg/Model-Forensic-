"""Experiment ledger: entries, automatic outcome, decisions, export, promotion to knowledge, API and CLI."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from model_forensic.application.ledger import LedgerService
from model_forensic.shared.errors import ValidationError

BASE, CAND = "local:baseline-cl4f-s0", "local:cand-radar-8f"


def test_entry_with_outcome_decision_and_export(container) -> None:  # type: ignore[no-untyped-def]
    led = LedgerService(container.docs, container.service)
    e = led.create("ana", {"title": "Radar branch, more FPN levels", "hypothesis": "far-range recall improves",
                           "baseline": BASE, "candidates": [CAND], "tags": ["radar"]})  # fmt: skip
    assert e["id"].startswith("L-") and e["status"] == "analysed"
    o = e["outcome"][CAND]
    assert o["config_changes"] and "moved" in o and o["significance"] in ("threshold", "replicates")
    assert any(
        p["id"] == "health:features" and p["cause"] == "data" for p in o["problems"]
    )  # the demo's dying radar scale is a health problem
    assert e["change_auto"]  # what changed, read from the config diff
    items, total = led.find()
    assert total == 1 and items[0]["headline"]
    d = led.update(e["id"], "ana", {"decision": "reject", "decision_note": "radar scale 2 collapses"})
    assert d["status"] == "decided" and d["history"][-1]["what"].startswith("changed")
    md = led.markdown()
    assert "## " + e["id"] in md and "**Hypothesis.** far-range recall improves" in md and "reject" in md
    assert led.find(status="decided")[1] == 1 and led.find(status="planned")[1] == 0


def test_validation_planned_entry_and_promotion(container) -> None:  # type: ignore[no-untyped-def]
    led = LedgerService(container.docs, container.service)
    with pytest.raises(ValidationError):
        led.create("ana", {"title": "x", "status": "nonsense"})
    with pytest.raises(ValidationError):
        led.create("ana", {"title": "x", "owner": "someone"})  # not an editable field
    p = led.create("ana", {"title": "Planned: longer schedule"})
    assert p["status"] == "planned" and p["outcome"] == {}
    with pytest.raises(ValidationError):
        led.refresh(p["id"])  # nothing to compare yet
    with pytest.raises(ValidationError):
        led.knowledge_yaml(p["id"])  # undecided
    e = led.create("ana", {"title": "Radar FPN", "baseline": BASE, "candidates": [CAND]})
    led.update(e["id"], "ana", {"decision": "adopt"})
    added, errors = container.knowledge.import_text(led.knowledge_yaml(e["id"]), via="ledger")
    assert added and added[0].status == "proposed" and not errors


def test_api_and_cli(container, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app
    from model_forensic.cli import main as cli

    c = TestClient(create_app(container))
    e = c.post("/api/v1/ledger", json={"title": "API entry", "baseline": BASE, "candidates": [CAND]}).json()
    assert c.get("/api/v1/ledger").json()["total"] == 1
    assert c.patch(f"/api/v1/ledger/{e['id']}", json={"decision": "follow-up"}).json()["status"] == "decided"
    assert c.patch(f"/api/v1/ledger/{e['id']}", json={"decision": "maybe"}).status_code in (400, 422)
    assert c.post(f"/api/v1/ledger/{e['id']}/refresh").json()["outcome"][CAND]
    assert "API entry" in c.get("/api/v1/ledger.md").text
    assert c.post(f"/api/v1/ledger/{e['id']}/promote").json()["added"]
    assert c.delete(f"/api/v1/ledger/{e['id']}").json()["deleted"] == e["id"]
    assert c.get(f"/api/v1/ledger/{e['id']}").status_code == 404

    monkeypatch.setattr(cli.base, "make_container", lambda _cfg=None: container)
    r = CliRunner()
    res = r.invoke(cli.app, ["ledger", "add", "--title", "CLI entry", "--baseline", BASE, "--candidate", CAND])
    assert res.exit_code == 0, res.output
    eid = res.output.split()[0]
    assert r.invoke(cli.app, ["ledger", "decide", eid, "adopt", "--note", "ok"]).exit_code == 0
    assert "CLI entry" in r.invoke(cli.app, ["ledger", "list"]).output
    out = tmp_path / "ledger.md"
    assert r.invoke(cli.app, ["ledger", "export", "--out", str(out)]).exit_code == 0 and "CLI entry" in out.read_text()
