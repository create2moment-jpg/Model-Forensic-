"""Harness adaptation: schema in sync, brief content, task contract, CLI round trip (brief → answer → import)."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from model_forensic.api.app import create_app
from model_forensic.application.harness import brief, harness_dir, schema, schema_text, tasks

REPO = Path(__file__).resolve().parents[3]


def test_schema_file_is_current() -> None:
    assert (REPO / "harness" / "knowledge.schema.json").read_text() == schema_text(), "run `mf harness schema`"
    s = schema()
    assert s["properties"]["entries"]["items"]["$ref"] == "#/$defs/KnowledgeEntry"


def test_tasks_and_rules_exist() -> None:
    t = tasks()
    assert {
        "setup_profiles",
        "loss_composition",
        "uncertainty_weighting",
        "label_targets",
        "metric_definitions",
        "checkpoint_and_validation",
        "backbone",
        "neck_and_fusion",
        "head_and_queries",
        "loss_study",
        "config_study",
    } <= set(t)
    assert (harness_dir() / "tasks" / "_rules.md").read_text().count("code_refs") >= 1


def test_brief_has_what_the_run_shows_and_the_task(container) -> None:
    container.knowledge.import_text("- {id: K-x, statement: something known}")
    text = brief(container.service, container.knowledge, "local:xf-pipe-finetune", "label_targets")
    for needle in (
        "## Run",
        "train_finetune, evaluate_pretrain, evaluate_finetune",
        "## Configuration",
        "## Loss and weighting series as logged",
        "## Other metrics as logged",
        "K-x (fact, proposed)",
        "# Task: label targets",
        "## Output contract",
    ):
        assert needle in text, needle
    assert "uncertainty_weight_log_sigma" in text  # raw logged names, as the code writes them


def test_unknown_task_and_api(container) -> None:
    c = TestClient(create_app(container))
    assert c.get("/api/v1/runs/local:xf-pipe-finetune/harness-brief", params={"task": "nope"}).status_code == 400
    r = c.get("/api/v1/runs/local:xf-pipe-finetune/harness-brief", params={"task": "loss_composition"})
    assert r.status_code == 200 and "# Task: loss composition" in r.text
    assert "label_targets" in c.get("/api/v1/harness/tasks").json()


def test_cli_round_trip(container, tmp_path, monkeypatch) -> None:
    from model_forensic.cli import main as cli

    monkeypatch.setattr(cli.base, "make_container", lambda _cfg=None: container)
    runner = CliRunner()
    out = tmp_path / "brief.md"
    assert (
        runner.invoke(
            cli.app, ["harness", "brief", "local:xf-pipe-finetune", "--task", "label_targets", "--out", str(out)]
        ).exit_code
        == 0
    )
    assert "Task: label targets" in out.read_text()
    answer = tmp_path / "answer.yaml"
    answer.write_text(
        "entries:\n  - id: K-manual-no-velocity\n    kind: constraint\n"
        "    statement: Manual labels carry no velocity targets.\n"
        "    when: {dataset_contains: [manual]}\n    effects: {ignore_metrics: ['*velocity*']}\n"
        "    source: {code_refs: ['data/labels/manual.py:88']}\n    confidence: high\n"
    )
    assert runner.invoke(cli.app, ["knowledge", "validate", str(answer)]).exit_code == 0
    res = runner.invoke(cli.app, ["knowledge", "import", str(answer), "--via", "harness:label_targets"])
    assert res.exit_code == 0 and "proposed" in res.stdout
    e = next(x for x in container.knowledge.load()[0] if x.id == "K-manual-no-velocity")
    assert e.status == "proposed" and e.source.via == "harness:label_targets"
    bad = tmp_path / "bad.yaml"
    bad.write_text("entries: [{id: x, statement: y}]")
    assert runner.invoke(cli.app, ["knowledge", "validate", str(bad)]).exit_code == 1
    assert json.loads(json.dumps(schema()))  # serialisable
