"""D5: the analyses' thresholds come from configuration; a team override changes the answers."""

from __future__ import annotations

import numpy as np

from model_forensic.analysis import thresholds as th
from model_forensic.analysis.loss_questions import ask
from model_forensic.domain.metrics import MetricKey, MetricSeries

N = 60
X = np.linspace(0, 1, N)


def _s(k: str, v: np.ndarray) -> MetricSeries:
    return MetricSeries(key=MetricKey.parse(k), steps=tuple(float(i) for i in range(N)), values=tuple(map(float, v)))


def _run() -> dict[str, MetricSeries]:
    a, b = np.exp(-3 * X) + 0.6, 0.4 * np.exp(-3 * X) + 0.1  # term a is ~85% of the summed terms at the end
    return {
        "train/loss": _s("train/loss", a + b),
        "train/loss/a": _s("train/loss/a", a),
        "train/loss/b": _s("train/loss/b", b),
    }


def test_defaults_table_and_override_change_the_answer() -> None:
    rows = {f"{r['section']}.{r['key']}": r for r in th.table()}
    assert (
        rows["loss_questions.term_share"]["default"] == 0.7 and rows["loss_questions.term_share"]["source"] == "default"
    )
    assert all(r["help"] and r["alarm_when"] in ("above", "below") for r in rows.values())
    share = {a.id: a for a in ask(_run())}["term_share"]
    assert share.status == "warn" and share.param == "loss_questions.term_share" and share.measure is not None
    assert share.alarm_when == "above" and 0.8 < share.measure < 0.9
    with th.override({"loss_questions": {"term_share": 0.9}}):
        assert {a.id: a for a in ask(_run())}["term_share"].status == "ok"
        assert {f"{r['section']}.{r['key']}": r for r in th.table()}["loss_questions.term_share"]["source"] == "team"
    assert {a.id: a for a in ask(_run())}["term_share"].status == "warn"  # back to the default


def test_configure_ignores_unknown_and_other_sections() -> None:
    with th.override({}):
        notes = th.configure({"loss_questions": {"nope": 1, "term_share": "x"}, "training_dynamics": {"spike_z": 3}})
        assert any("nope" in n for n in notes) and any("not a number" in n for n in notes)
        assert th.t("loss_questions", "term_share") == 0.7


def test_team_policy_reaches_the_analyses(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app
    from model_forensic.container import build
    from model_forensic.settings import Settings

    team = tmp_path / "team.yaml"
    team.write_text("analysis_params:\n  internals: {starved_max: 0.2}\n")
    monkeypatch.setenv("MF_TEAM_CONFIG", str(team))
    monkeypatch.setenv("MF_HOME", str(tmp_path))
    with th.override({}):
        c = build(Settings.load(None))
        assert th.t("internals", "starved_max") == 0.2
        rows = TestClient(create_app(c)).get("/api/v1/settings/thresholds").json()["thresholds"]
        assert any(r["key"] == "starved_max" and r["source"] == "team" for r in rows)
