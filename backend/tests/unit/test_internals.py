"""Model internals: attention per layer × modality, query statistics, notes, questions, API."""

from __future__ import annotations

import numpy as np

from model_forensic.analysis.internals import attention_table, internals, query_table
from model_forensic.analysis.loss_questions import ask
from model_forensic.domain.metrics import MetricKey, MetricSeries

N = 40


def _s(key: str, values: np.ndarray) -> MetricSeries:
    return MetricSeries(
        key=MetricKey.parse(key), steps=tuple(float(i) for i in range(N)), values=tuple(map(float, values))
    )


def _run(radar: np.ndarray, entropy: float = 0.6, memory: float = 0.4) -> dict[str, MetricSeries]:
    s: dict[str, MetricSeries] = {}
    for k in range(4):
        cam = 1 - radar
        s[f"train/attn_mass[layer={k},modality=camera]"] = _s(f"train/attn_mass[layer={k},modality=camera]", cam)
        s[f"train/attn_mass[layer={k},modality=radar]"] = _s(f"train/attn_mass[layer={k},modality=radar]", radar)
        s[f"train/attn_entropy[layer={k}]"] = _s(f"train/attn_entropy[layer={k}]", np.full(N, entropy))
    s["train/q_from_memory_frac"] = _s("train/q_from_memory_frac", np.full(N, memory))
    s["train/q_pos_frac[source=propagated]"] = _s("train/q_pos_frac[source=propagated]", np.full(N, 0.02))
    s["train/q_pos_frac[source=new]"] = _s("train/q_pos_frac[source=new]", np.full(N, 0.06))
    return s


def test_table_and_healthy_internals() -> None:
    s = _run(np.full(N, 0.3))
    t = attention_table(s)
    assert t["layers"] == ["0", "1", "2", "3"] and t["modalities"] == ["camera", "radar"]
    assert abs(t["mass"]["2"]["radar"]["end"] - 0.3) < 1e-6
    assert {r["key"] for r in query_table(s)} >= {"train/q_from_memory_frac"}
    ids = {n["id"] for n in internals(s)["notes"]}
    assert not any(i.startswith(("starved", "collapse", "faded")) for i in ids)
    assert "memory_stale" in ids  # propagated matched at a third of the rate of new queries


def test_starved_faded_collapsed_and_unused_memory() -> None:
    fading = np.concatenate([np.full(N // 2, 0.3), np.linspace(0.3, 0.01, N // 2)])
    d = internals(_run(fading, entropy=0.01, memory=0.03))
    ids = {n["id"] for n in d["notes"]}
    assert {"faded:radar", "collapse:0", "memory_unused"} <= ids
    starved = internals(_run(np.full(N, 0.02)))
    assert any(n["id"] == "starved:radar" and n["status"] == "bad" for n in starved["notes"])
    a = {x.id: x for x in ask(_run(np.full(N, 0.02)))}
    assert a["attention"].status == "bad" and "radar" in a["attention"].answer
    assert a["memory"].status == "warn"
    empty = {x.id: x for x in ask({"train/loss": _s("train/loss", np.linspace(2, 1, N))})}
    assert empty["attention"].status == "na" and empty["memory"].status == "na"


def test_api_on_demo(container) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    c = TestClient(create_app(container))
    d = c.get("/api/v1/runs/local:cand-radar-8f/internals", params={"baseline": "local:baseline-cl4f-s0"}).json()
    assert d["logged"] and "radar" in d["attention"]["modalities"] and d["baseline"]["attention"]["layers"]
    assert any(n["id"].startswith(("starved:radar", "faded:radar")) for n in d["notes"])  # the demo's collapsing radar
