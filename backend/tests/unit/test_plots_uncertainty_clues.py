"""Plot specs (schema + grounding), the uncertainty-weighting section and the training setup clues."""

from __future__ import annotations

from fastapi.testclient import TestClient

UW = "local:xf-sparse-uw"
PLAIN = "local:baseline-cl4f-s0"


def _api(container):  # type: ignore[no-untyped-def]
    from model_forensic.api.app import create_app

    return TestClient(create_app(container))


def test_plot_specs_are_schema_checked_and_grounded(container) -> None:  # type: ignore[no-untyped-def]
    api = _api(container)
    assert api.get("/api/v1/plots/schema").json()["properties"]["series"]["maxItems"] == 12
    sug = api.get(f"/api/v1/runs/{PLAIN}/plots/suggest").json()["specs"]
    assert sug and all(s["source"] == "rules" for s in sug)
    for s in sug:  # every suggestion validates and renders
        v = api.post("/api/v1/plots/validate", json=s).json()
        assert v["ok"], v["errors"]
        r = api.post("/api/v1/plots/render", json=s).json()
        assert len(r["series"]) == len(s["series"]) and r["series"][0]["x"]
    key = sug[0]["series"][0]["key"]
    good = {"id": "x", "title": "t", "series": [{"run": PLAIN, "key": key}]}
    assert api.post("/api/v1/plots/validate", json=good).json()["ok"]
    unknown = {**good, "series": [{"run": PLAIN, "key": "val/does_not_exist"}]}
    v = api.post("/api/v1/plots/validate", json=unknown).json()
    assert not v["ok"] and "is not a metric of" in v["errors"][0]
    extra = {**good, "colour": "red"}
    assert "colour" in api.post("/api/v1/plots/validate", json=extra).json()["errors"][0]  # strict schema
    too_many = {**good, "series": [{"run": PLAIN, "key": key}] * 13}
    assert not api.post("/api/v1/plots/validate", json=too_many).json()["ok"]
    norun = {**good, "series": [{"run": "local:nope", "key": key}]}
    assert "not found" in api.post("/api/v1/plots/validate", json=norun).json()["errors"][0]
    assert api.post("/api/v1/plots/render", json=unknown).status_code == 422  # never drawn


def test_log_axis_with_non_positive_values_is_refused(container) -> None:  # type: ignore[no-untyped-def]
    api = _api(container)
    rep = api.get(f"/api/v1/runs/{UW}/uncertainty").json()
    key = rep["heads"][0]["key"]  # log σ can be negative
    spec = {"id": "s", "title": "t", "y": {"scale": "log"}, "series": [{"run": UW, "key": key}]}
    v = api.post("/api/v1/plots/validate", json=spec).json()
    if not v["ok"]:
        assert "log axis" in v["errors"][0]
    spec["series"][0]["transform"] = "exp_neg2"  # weights are positive: fine on a log axis
    assert api.post("/api/v1/plots/validate", json=spec).json()["ok"]


def test_ai_plotter_without_llm_falls_back_to_rules(container) -> None:  # type: ignore[no-untyped-def]
    r = _api(container).post(f"/api/v1/runs/{PLAIN}/plots/ai", json={"question": "is it overfitting?"}).json()
    assert r["specs"] == [] and r["fallback"] and "no LLM" in r["note"]


def test_ai_plotter_output_is_validated(container) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.application.plotting import suggest_ai
    from model_forensic.application.ports import LLMResponse

    key = str(container.service.series(PLAIN)[0].key)

    class Fake:
        model = "fake"

        def complete(self, _msgs, **_k):  # type: ignore[no-untyped-def]
            return LLMResponse(tool_calls=[], content=f'Here: [{{"id":"a","title":"A","series":[{{"run":"{PLAIN}","key":"{key}"}}]}},'
                                f'{{"id":"b","title":"B","series":[{{"run":"{PLAIN}","key":"val/invented"}}]}}]', model="fake")  # fmt: skip

    container.llm = Fake()
    out = suggest_ai(container, PLAIN, "q")
    assert [s["id"] for s in out["specs"]] == ["a"] and out["specs"][0]["source"] == "ai"
    assert out["rejected"][0]["spec"]["id"] == "b" and "val/invented" in out["rejected"][0]["errors"][0]


def test_uncertainty_section(container) -> None:  # type: ignore[no-untyped-def]
    api = _api(container)
    rep = api.get(f"/api/v1/runs/{UW}/uncertainty").json()
    assert rep["available"] and rep["summary"]["heads"] >= 3
    shares = [h["share"] for h in rep["heads"]]
    assert abs(sum(shares) - 1) < 0.01 and shares == sorted(shares)
    off = [h for h in rep["heads"] if h["status"] == "switched_off"]
    assert off and off[0]["term"] == "vel" and "stopped training it" in off[0]["why"]  # the demo's give-up head
    h = rep["heads"][0]
    assert h["weight"]["last"] > 0 and "log_sigma" in h and h["drift_last_10pct"] is not None
    assert {p["id"] for p in rep["plots"]} >= {"uw-log-sigma", "uw-weight"} and rep["findings"]
    assert "loss.uncertainty_weighting" in rep["config"] and not any("composed_config" in k for k in rep["config"])
    none = api.get(f"/api/v1/runs/{PLAIN}/uncertainty").json()
    assert none["available"] is False and none["message"]["severity"] == "missing_data"


def test_bounded_sigma_is_reported_at_bound(container) -> None:  # type: ignore[no-untyped-def]
    rep = _api(container).get("/api/v1/runs/local:xf-sparse-uw-bounded/uncertainty").json()
    assert rep["bounds"] == [-1.0, 0.5]
    vel = [h for h in rep["heads"] if h["term"] == "vel"]
    assert vel and all(h["status"] in ("at_bound", "settled", "drifting") for h in vel)
    assert not any(h["status"] == "switched_off" and h["term"] == "vel" for h in rep["heads"])


def test_setup_clues_cite_sources(container) -> None:  # type: ignore[no-untyped-def]
    d = _api(container).get(f"/api/v1/runs/{UW}/setup-clues").json()
    c = {x["id"]: x for x in d["clues"]}
    assert c["optimizer"]["value"] == "adamw" and c["optimizer"]["source"].startswith("config:")
    assert c["batch_size"]["value"] == 16 and "gpu_type" in c
    assert all(x["source"].split(":", 1)[0] in ("config", "metric", "run") for x in d["clues"])
    eff = next(o for o in d["observations"] if o["id"] == "effective_batch")
    assert "128" in eff["text"] and eff["evidence"]
    assert "Gradient clipping" in d["not_found"]  # not found is said, never guessed


def test_uncertainty_over_time_theory_and_baseline(container) -> None:  # type: ignore[no-untyped-def]
    api = _api(container)
    rep = api.get(f"/api/v1/runs/{UW}/uncertainty", params={"baseline": "local:xf-sparse-uw-bounded"}).json()
    tl = rep["timeline"]
    n = len(tl["steps"])
    assert n >= 3 and all(len(v) == n for v in tl["share"].values())
    import math

    mid = n // 2
    assert math.isclose(sum(v[mid] for v in tl["share"].values()), 1.0, abs_tol=1e-3)  # shares sum to one
    bs = tl["balance_summary"]
    assert 1 <= bs["effective_tasks_end"] <= bs["heads"] and 0 <= bs["balance_end"] <= 1
    vel = next(h for h in rep["heads"] if h["term"] == "vel")
    assert vel["switched_off_at"] is not None and vel["share_min"] <= vel["share"] <= vel["share_max"]
    assert vel["optimum_gap"] is not None and vel["optimum_gap"] > 0  # σ above the optimum: under-weighted
    assert vel["share_delta"] < 0 and rep["baseline"]["run"] == "local:xf-sparse-uw-bounded"
    assert tl["contribution"] is not None
    none = api.get(f"/api/v1/runs/{UW}/uncertainty", params={"baseline": PLAIN}).json()
    assert "no learned task weights" in none["baseline"]["error"]
