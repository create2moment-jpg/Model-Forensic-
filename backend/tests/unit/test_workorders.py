"""Work orders (ADR-0013): the registry is complete and consistent, briefs are scoped, outputs are checked,
escalation packets are compact."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from model_forensic.application import workorders as wo
from model_forensic.application.harness import brief, harness_dir, tasks
from model_forensic.cli.main import app

ROOT = harness_dir().parent


def _cli_commands() -> set[str]:
    names = {c.name or (c.callback.__name__ if c.callback else "") for c in app.registered_commands}
    names |= {g.name for g in app.registered_groups if g.name}
    return names


def test_registry_is_complete_and_consistent() -> None:
    orders = wo.load()
    assert len(orders) >= 20
    for oid, w in orders.items():
        assert oid.startswith("WO-") and w.id == oid
        assert (ROOT / w.prompt).is_file(), f"{oid}: prompt {w.prompt} missing"
        if w.output.schema_:
            assert (ROOT / w.output.schema_).is_file(), f"{oid}: schema missing"
        if w.task:
            assert w.task in tasks(), f"{oid}: unknown harness task {w.task}"
        v = w.validate_
        if w.status == "ready" and v.startswith("mf "):
            assert v.split()[1] in _cli_commands(), f"{oid}: validation command {v!r} does not exist"
    # every operator prompt is reachable through a work order
    covered = {w.prompt for w in orders.values()}
    for t in tasks():
        assert f"harness/tasks/{t}.md" in covered, f"harness task {t} has no work order"
    for p in (ROOT / "setup-agent" / "prompts").glob("*.md"):
        if p.name != "README.md":
            assert f"setup-agent/prompts/{p.name}" in covered, f"{p.name} has no work order"


def test_briefs_put_the_task_first_and_cut_nothing(container) -> None:  # type: ignore[no-untyped-def]
    import re

    run = "local:xf-pipe-finetune"
    full = brief(container.service, container.knowledge, run, task="loss_expectations", focus={})
    focused = brief(container.service, container.knowledge, run, task="loss_expectations")
    lines = lambda t: set(re.findall(r"^- `[^`]+`(?: = `[^`]*`)?$", t, re.M))  # noqa: E731
    assert lines(full) == lines(focused)  # the same keys and series — only the order differs
    first, _, rest = focused.partition("## Everything else (complete)")
    assert "## What this task needs first" in first and rest
    assert re.search(r"## Loss and weighting series as logged — for this task \((\d+)\)", first)
    assert int(re.search(r"for this task \((\d+)\)", first).group(1)) > 0  # type: ignore[union-attr]


def test_cards_carry_the_method_and_self_checks() -> None:
    w = wo.load()["WO-loss-expectations"]
    c = wo.card(w)
    assert "Collect first, decide second" in c and "## Before you validate, check" in c
    assert all(item in c for item in w.checklist) and len(w.checklist) >= 3


def test_outputs_are_checked_against_the_contract(tmp_path: Path) -> None:
    orders = wo.load()
    k = tmp_path / "k.yaml"
    k.write_text(yaml.safe_dump({"entries": [{"id": "K-x", "kind": "fact", "statement": "The cls loss is focal."}]}))
    assert any("code_refs" in e for e in wo.check(orders["WO-loss-composition"], k))
    k.write_text(yaml.safe_dump({"entries": [{"id": "K-x", "kind": "fact", "statement": "The cls loss is focal.",
                                              "source": {"code_refs": ["heads/h.py:12"]}}]}))  # fmt: skip
    assert wo.check(orders["WO-loss-composition"], k) == []
    e = tmp_path / "e.yaml"
    e.write_text(yaml.safe_dump({"terms": [{"name": "train/loss/cls", "code_refs": ["h.py:1"]}]}))
    assert any("matches no run" in x for x in wo.check(orders["WO-loss-expectations"], e))
    dossier = tmp_path / "d.md"
    dossier.write_text("- [E-Q1] a\n- [E-Q2] b\n- [E-X1] c\n")
    rep = tmp_path / "r.md"
    rep.write_text("## Diagnosis\n[E-Q1] [E-Q2] [E-Z9]\n## Observations\n## Differential\n## Where to look\n"
                   "## Next steps\n## Confidence\n")  # fmt: skip
    errs = wo.check(orders["WO-interpret-dossier"], rep, dossier)
    assert errs == ["cites ids that are not in the dossier: E-Z9"]
    assert wo.check(orders["WO-interpret-dossier"], tmp_path / "none.md") == [f"{tmp_path / 'none.md'}: no such file"]


def test_escalation_packet_is_compact(tmp_path: Path) -> None:
    w = wo.load()["WO-loss-expectations"]
    out = tmp_path / "big.yaml"
    out.write_text("terms: []\n" + "# filler\n" * 5000)
    text = wo.escalate(w, out, note="the weights are set in two places")
    assert "## What the validator says" in text and "no terms" in text and "two places" in text
    assert len(text) // 4 < 2500


def test_cli(container, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.cli import main as cli

    monkeypatch.setattr(cli, "_c", lambda _cfg=None: container)
    r = CliRunner()
    assert "WO-loss-expectations" in r.invoke(app, ["work", "list"]).output
    assert "Validate:" in r.invoke(app, ["work", "show", "WO-filter-facets"]).output
    assert r.invoke(app, ["work", "show", "WO-nope"]).exit_code != 0
    b = tmp_path / "b.md"
    res = r.invoke(app, ["work", "brief", "WO-loss-expectations", "--run", "local:xf-pipe-finetune", "--out", str(b)])
    assert res.exit_code == 0 and b.read_text().startswith("# WO-loss-expectations")
    bad = tmp_path / "bad.yaml"
    bad.write_text("entries: []\n")
    assert r.invoke(app, ["work", "check", "WO-backbone", str(bad)]).exit_code == 1
    res = r.invoke(app, ["work", "escalate", "WO-backbone", "--file", str(bad), "--note", "stuck"])
    assert res.exit_code == 0 and "escalations" in res.output
