"""Matching instability and logit growth (kit: matching.py, stability.py): the internals table, notes and the two
health questions."""

from __future__ import annotations

import numpy as np

from model_forensic.analysis.internals import internals, stability_notes, stability_table
from model_forensic.analysis.loss_questions import ask
from model_forensic.domain.metrics import MetricKey, MetricSeries

N = 80
x = np.linspace(0, 1, N)


def _s(key: str, v: np.ndarray) -> MetricSeries:
    return MetricSeries(key=MetricKey.parse(key), steps=tuple(float(i) for i in range(N)), values=tuple(map(float, v)))


def runs(unstable: bool, growing: bool) -> dict[str, MetricSeries]:
    out = {}
    for k in range(3):
        mi = np.full(N, 0.3) if unstable else 0.35 * np.exp(-4 * x) + 0.04
        out[f"val/match_instability[layer={k}]"] = _s(f"val/match_instability[layer={k}]", mi)
        if k:
            lc = np.full(N, 0.4 if unstable else 0.05)
            out[f"val/match_layer_change[layer={k}]"] = _s(f"val/match_layer_change[layer={k}]", lc)
        al = 10 * (1 + 5 * x**2) if growing and k == 2 else 10 * (1 + 0.1 * x)
        out[f"train/attn_logit_max[layer={k}]"] = _s(f"train/attn_logit_max[layer={k}]", al)
    out["train/logit_max[layer=2]"] = _s("train/logit_max[layer=2]", 6 + x)
    return out


def test_table_and_notes() -> None:
    st = stability_table(runs(True, True))
    assert set(st) == {"match_instability", "match_layer_change", "attn_logit_max", "logit_max"}
    assert list(st["match_instability"]) == ["0", "1", "2"] and st["attn_logit_max"]["2"]["rising"]
    ids = {n["id"]: n for n in stability_notes(st)}
    assert ids["matching_unstable"]["status"] == "bad"  # unstable and not falling
    assert "matching_layers" in ids and "logit_growth:attn_logit_max" in ids
    assert "logit_growth:logit_max" not in ids  # the output logits stay in range
    assert stability_notes(stability_table(runs(False, False))) == []


def test_questions_and_internals_view() -> None:
    bad = {a.id: a for a in ask(runs(True, True))}
    assert bad["matching"].status == "bad" and bad["matching"].param == "internals.match_unstable"
    assert bad["logits"].status == "warn" and "×" in bad["logits"].answer
    good = {a.id: a for a in ask(runs(False, False))}
    assert good["matching"].status == "ok" and good["logits"].status == "ok"
    none = {a.id: a for a in ask({"train/loss": _s("train/loss", 1 - x / 2)})}
    assert none["matching"].status == "na" and none["logits"].status == "na"
    view = internals(runs(True, True), runs(False, False))
    assert view["logged"] and "stability" in view["baseline"]
    assert any(n["id"] == "matching_unstable" for n in view["notes"])


def test_demo_candidate_has_the_problems(container) -> None:  # type: ignore[no-untyped-def]
    svc = container.service
    view = {str(s.key): s for s in svc.series("local:cand-radar-8f")}
    got = {a.id: a.status for a in ask(view)}
    assert got["matching"] in ("bad", "warn") and got["logits"] == "warn"
    base = {a.id: a.status for a in ask({str(s.key): s for s in svc.series("local:baseline-cl4f-s0")})}
    assert base["matching"] == "ok" and base["logits"] == "ok"
