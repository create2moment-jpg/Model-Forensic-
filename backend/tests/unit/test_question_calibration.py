"""D6: verdicts on real runs → proposed thresholds → applied to the team policy."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from model_forensic.analysis import thresholds as th
from model_forensic.application import question_calibration as qc


def _item(run: str, measure: float, verdict: str, param: str = "loss_questions.term_share", why: str = "x") -> dict:  # type: ignore[type-arg]
    return {"run": run, "question": "term_share", "status": "warn", "measure": measure, "param": param,
            "alarm_when": th.alarm_when(*param.split(".", 1)), "verdict": verdict, "why": why}  # fmt: skip


def _write(p: Path, items: list[dict], missed: list[dict] | None = None) -> Path:  # type: ignore[type-arg]
    p.write_text(yaml.safe_dump({"items": items, "missed": missed or []}))
    return p


def test_template_lists_the_alarms_with_measures(container) -> None:  # type: ignore[no-untyped-def]
    text = qc.template(container.service, ["local:cand-radar-8f", "local:baseline-cl4f-s0"])
    cal = qc.parse(text)
    assert cal.items and all(it.verdict == "" for it in cal.items)
    assert any(it.param and it.measure is not None for it in cal.items)  # calibratable alarms carry their measure
    assert "# questions:" in text and "verdict: true" in text  # the instructions travel with the file


def test_check_requires_verdicts_and_reasons(tmp_path: Path) -> None:
    f = _write(tmp_path / "c.yaml", [_item("r1", 0.75, ""), _item("r2", 0.8, "false", why="")],
               [{"run": "r3", "question": "nope", "why": ""}])  # fmt: skip
    errs = qc.check(f)
    assert any("verdict missing" in e for e in errs) and any("`why` is required for false" in e for e in errs)
    assert any("unknown question 'nope'" in e for e in errs)
    ok = _write(tmp_path / "ok.yaml", [_item("r1", 0.75, "true"), _item("r2", 0.8, "false")])
    assert qc.check(ok) == []


def test_propose_separates_false_alarms_from_real_problems(tmp_path: Path) -> None:
    with th.override({}):
        f = _write(tmp_path / "c.yaml", [_item("a", 0.72, "false"), _item("b", 0.74, "false"), _item("c", 0.9, "true")])
        p = qc.propose([f])
        row = next(r for r in p["rows"] if r["param"] == "loss_questions.term_share")
        assert row["proposed"] is not None and 0.74 < row["proposed"] < 0.9  # between the false alarms and the real one
        assert p["analysis_params"] == {"loss_questions": {"term_share": row["proposed"]}}
        conflict = _write(tmp_path / "x.yaml", [_item("a", 0.9, "false"), _item("b", 0.8, "true")])
        r2 = qc.propose([conflict])["rows"][0]
        assert r2["proposed"] is None and "cannot separate" in r2["reason"]
        few = qc.propose([_write(tmp_path / "f.yaml", [_item("a", 0.9, "false")])])["rows"][0]
        assert few["proposed"] is None and "at least 2" in few["reason"]
        # a 'below' threshold mirrors: flat terms (alarm when the change is below 0.01)
        below = [_item("a", 0.008, "false", "loss_questions.term_flat_change"),
                 _item("b", 0.002, "true", "loss_questions.term_flat_change")]  # fmt: skip
        rb = qc.propose([_write(tmp_path / "b.yaml", below)])["rows"][0]
        assert rb["proposed"] is not None and 0.002 < rb["proposed"] < 0.008


def test_apply_merges_into_the_team_policy_and_cli(tmp_path: Path, monkeypatch, container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.cli import main as cli

    team = tmp_path / "team.local.yaml"
    team.write_text("deprioritized_classes: [bicycle]\nanalysis_params:\n  internals: {starved_max: 0.1}\n")
    proposal = tmp_path / "proposal.yaml"
    proposal.write_text(qc.proposal_yaml({"rows": [], "analysis_params": {"loss_questions": {"term_share": 0.8}}}))
    qc.apply(proposal, team)
    doc = yaml.safe_load(team.read_text())
    assert doc["deprioritized_classes"] == ["bicycle"]
    assert doc["analysis_params"] == {"internals": {"starved_max": 0.1}, "loss_questions": {"term_share": 0.8}}
    assert (tmp_path / "team.local.yaml.bak").exists()
    monkeypatch.setattr(cli.base, "make_container", lambda _cfg=None: container)
    r = CliRunner()
    out = tmp_path / "q.yaml"
    res = r.invoke(cli.app, ["calibrate", "template", "--runs", "local:cand-radar-8f", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert r.invoke(cli.app, ["calibrate", "check", str(out)]).exit_code == 1  # verdicts not filled yet
    assert "WO-calibrate-questions" in r.invoke(cli.app, ["work", "list"]).output
    assert r.invoke(cli.app, ["thresholds", "--section", "internals"]).exit_code == 0
