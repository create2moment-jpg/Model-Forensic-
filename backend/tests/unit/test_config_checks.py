"""Configuration checks: stable ids, current vs expected, remediation, a prompt that exists, a validation command."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from model_forensic.application.config_checks import run, summary

ROOT = Path(__file__).parents[3]
PROMPTS = ROOT / "setup-agent" / "prompts"
NINE = {
    "run_light_heavy_audit.md", "test_space_profile.md", "test_space_hypothesis.md",
    "prefetch_observability_diagnostics.md", "plot_selection.md", "uncertainty_loss_forensics.md",
    "interface_diagnostics.md", "metric_columns_diagnostics.md", "configuration_check_and_repair.md",
    "filter_facets.md",
}  # fmt: skip


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MF_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_every_check_is_complete_and_maps_to_a_prompt(container) -> None:  # type: ignore[no-untyped-def]
    checks = run(container)
    ids = [c.id for c in checks]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"CFG-[A-Z]+-\d", i) for i in ids)
    for c in checks:
        assert c.severity in ("critical", "warning", "info") and c.status in ("pass", "fail", "unknown", "skip")
        assert c.current and c.expected and c.remediation and c.validation, c.id
        assert (PROMPTS / c.prompt).is_file(), c.prompt
        assert c.id in (PROMPTS / c.prompt).read_text() or c.prompt == "configuration_check_and_repair.md"
    rep = summary(checks)
    assert rep["checks"][0]["prompt_path"].startswith("setup-agent/prompts/")


def test_failures_say_current_and_expected(container, _home: Path) -> None:  # type: ignore[no-untyped-def]
    container.backend_errors["ws-x"] = "ClientAuthenticationError: token expired"
    (_home / "config").mkdir(parents=True)
    (_home / "config" / "tasks.local.yaml").write_text("tasks:\n  run.nope:\n    cost: heavy\n")
    by = {c.id: c for c in run(container)}
    assert by["CFG-WS-1"].status == "fail" and "token expired" in by["CFG-WS-1"].current
    assert by["CFG-TASK-1"].status == "fail" and "run.nope" in by["CFG-TASK-1"].current
    assert not summary(list(by.values()))["ok"]  # a critical failure
    (_home / "config" / "tasks.local.yaml").write_text("tasks:\n  run.metrics:\n    cost: enormous\n")
    assert "cost must be one of" in {c.id: c for c in run(container)}["CFG-TASK-1"].current


def test_api_and_cli(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    d = TestClient(create_app(container)).get("/api/v1/checks").json()
    assert {"checks", "failed", "critical", "ok"} <= set(d)
    assert any(c["id"] == "CFG-PF-1" and c["status"] != "skip" for c in d["checks"])  # the server sees prefetch


def test_cli_checks_json(tmp_path: Path, demo_root: Path) -> None:
    import json

    from model_forensic.cli.main import app

    cfg = tmp_path / "mf.yaml"
    cfg.write_text(
        f"data_dir: {tmp_path / '.mf'}\nbackends:\n  - kind: local\n    name: local\n    options: {{root: {demo_root}}}\n"
    )
    r = CliRunner().invoke(app, ["checks", "--json", "-c", str(cfg)])
    out = json.loads(r.output)
    assert r.exit_code in (0, 1) and out["checks"] and {c["id"] for c in out["checks"]} >= {"CFG-WS-1", "CFG-GIT-1"}


def test_the_nine_prompts_and_the_table() -> None:
    assert {p.name for p in PROMPTS.glob("*.md")} - {"README.md"} == NINE
    table = (PROMPTS / "README.md").read_text()
    for name in NINE:
        assert f"]({name})" in table, name
        text = (PROMPTS / name).read_text()
        for section in ("## Inputs", "## Task", "## Output", "## Scope", "## Validation", "## Rules (always)"):
            assert section in text, (name, section)
        assert "Read-only towards Azure" in text and "No site details in git" in text
    assert "| Problem | Prompt | Checks | Scope (may change) | Validation |" in table
