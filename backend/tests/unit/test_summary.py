"""A comparison as Markdown for a pull request — the same facts the Compare page shows."""

from __future__ import annotations

from fastapi.testclient import TestClient

from model_forensic.application.summary import compare_markdown
from model_forensic.application.verdict import compare_verdict
from model_forensic.container import Container

B, C = "local:baseline-cl4f-s2", "local:query-1800-spiky"


def test_markdown_has_the_verdict_metrics_changes_and_how_to_reproduce(container: Container) -> None:
    md = compare_markdown(container.service, B, C)
    assert compare_verdict(container.service, B, C)["sentence"] in md
    assert "| metric | baseline | candidate | change |" in md
    assert "| NDS |" in md.split("**What changed")[0]  # headline metrics first
    assert "model.num_queries" in md and f"mf summary --baseline {B} --candidate {C}" in md
    table = md.split("**Curves**")[0]
    rows = [ln for ln in table.splitlines() if ln.startswith("| ") and "metric" not in ln]
    assert len(rows) <= 13  # 12 + "… more"
    # curves as text: the training loss of both runs on one scale
    curves = md.split("**Curves**")[1].split("**What changed")[0]
    assert "| train loss | `" in curves and all(ch in "▁▂▃▄▅▆▇█`| " for ch in curves.splitlines()[-2].split("|", 2)[2])


def test_spark_text() -> None:
    from model_forensic.application.summary import spark_text

    assert spark_text([0, 1, 2, 3, 4, 5, 6, 7], 0, 7) == "▁▂▃▄▅▆▇█"
    assert spark_text([5, 5], 0, 10) == "▄▄" and spark_text([1], 0, 1) == ""
    assert len(spark_text(list(range(100)), 0, 99, width=24)) == 24


def test_route_and_cli(container: Container, monkeypatch: object) -> None:
    from typer.testing import CliRunner

    from model_forensic.api.app import create_app
    from model_forensic.cli import main as cli

    r = TestClient(create_app(container)).get("/api/v1/compare/summary.md", params={"baseline": B, "candidate": C})
    assert r.status_code == 200 and r.text.startswith("### ")
    monkeypatch.setattr(cli.base, "make_container", lambda _cfg=None: container)  # type: ignore[attr-defined]
    res = CliRunner().invoke(cli.app, ["summary", "--baseline", B, "--candidate", C])
    assert res.exit_code == 0 and "| metric |" in res.stdout
