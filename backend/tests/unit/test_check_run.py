"""Beginner "Check my run": at most five plain-language cards, jargon only in learn-more."""

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.application.check_run import GLOSSARY, MAX_CARDS, check_run


def test_check_pipeline_run(container) -> None:
    r = check_run(container.service, "local:xf-pipe-finetune")
    assert r["overall"] == "red" and 0 < len(r["cards"]) <= MAX_CARDS
    titles = [c["title"] for c in r["cards"]]
    assert "The 'best' checkpoint is not really the best" in titles
    assert not any(t.startswith("P") and t[1].isdigit() for t in titles)  # no check ids for beginners
    for c in r["cards"]:
        assert c["light"] in {"red", "amber", "green"} and c["meaning"] and c["why"] and c["todo"]
    assert set(r["glossary"]) <= set(GLOSSARY)


def test_check_default_run_and_api(container) -> None:
    r = check_run(container.service)  # newest completed run
    assert r["run"]["status"] == "completed" and r["cards"]
    c = TestClient(create_app(container))
    assert c.get("/api/v1/check", params={"run": "local:baseline-cl4f-s0"}).json()["cards"]
    assert len(c.get("/api/v1/glossary").json()) >= 20


def test_cli_check(container, monkeypatch) -> None:
    import json

    from typer.testing import CliRunner

    from model_forensic.cli import main as cli

    monkeypatch.setattr(cli.base, "make_container", lambda _cfg=None: container)
    res = CliRunner().invoke(cli.app, ["check", "local:xf-pipe-finetune", "--json"])
    assert res.exit_code == 0, res.output
    doc = json.loads(res.stdout)
    assert doc["overall"] == "red" and 1 <= len(doc["cards"]) <= 5
    res = CliRunner().invoke(cli.app, ["check", "local:xf-pipe-finetune"])
    assert res.exit_code == 1 and "Next:" in res.stdout
