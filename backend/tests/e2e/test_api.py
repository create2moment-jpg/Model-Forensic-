from fastapi.testclient import TestClient

from model_forensic.api.app import create_app


def client(container) -> TestClient:
    return TestClient(create_app(container))


def test_search_inspect_compare_analyze_investigate_report(container) -> None:
    c = client(container)
    assert c.get("/api/v1/health").json()["ok"]

    runs = c.get("/api/v1/runs", params={"q": "tag:role=candidate"}).json()
    assert {r["name"] for r in runs["runs"]} == {"cand-radar-8f", "cand-uncert", "sam-radar-live"}

    ov = c.get("/api/v1/runs/cand-radar-8f").json()
    assert ov["baseline"].startswith("local:baseline-cl4f-s")  # auto-suggested baseline
    paths = {ch["path"] for ch in ov["what_changed"]["config_diff"]["changes"]}
    assert {"temporal.num_frames", "optimizer.lr", "model.fusion.modalities"} <= paths

    m = c.get("/api/v1/runs/cand-radar-8f/metrics", params={"keys": ["train/loss"], "max_points": 50}).json()
    assert m[0]["key"] == "train/loss" and len(m[0]["steps"]) <= 60

    cmp = c.get("/api/v1/compare", params={"baseline": "baseline-cl4f-s0", "candidate": "cand-radar-8f"}).json()
    assert len(cmp["baseline_replicates"]) == 2
    worst = {d["metric"]: d for d in cmp["deltas"]}["val/recall[range=60-inf]"]
    assert worst["verdict"] == "regressed" and worst["significance_basis"] == "replicates"

    res = c.post("/api/v1/analyze", json={"run": "cand-radar-8f", "baseline": "baseline-cl4f-s0"}).json()
    by = {r["analysis"]: r for r in res}
    assert any("range=60-inf" in f["title"] for f in by["regression"]["findings"])
    assert by["config_attribution"]["findings"][0]["label"] == "hypothesized"

    inv = c.post(
        "/api/v1/investigate", json={"run": "cand-radar-8f", "baseline": "baseline-cl4f-s0", "prompt_id": "why_worse"}
    ).json()
    assert inv["claims"] and inv["verified_ratio"] == 1.0 and inv["prompt"] == "why_worse@1"
    assert all(cl["label"] == "ai_generated" for cl in inv["claims"])

    rep = c.post(
        "/api/v1/reports", json={"run": "cand-radar-8f", "baseline": "baseline-cl4f-s0", "investigation_id": inv["id"]}
    )
    for section in [
        "Executive Summary",
        "Configuration Differences",
        "Metric Comparison",
        "Hypotheses",
        "Recommended Experiments",
        "Limitations",
        "Provenance",
    ]:
        assert f"## {section}" in rep.text or f"### {section}" in rep.text
    html = c.post("/api/v1/reports", json={"run": "lr-sweep-1e3", "format": "html"})
    assert html.headers["content-type"].startswith("text/html") and "<table>" in html.text


def test_errors_are_structured(container) -> None:
    r = client(container).get("/api/v1/runs/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_failed_run_investigation_includes_untrusted_log(container) -> None:
    inv = client(container).post("/api/v1/investigate", json={"run": "lr-sweep-1e3", "prompt_id": "why_failed"}).json()
    assert inv["claims"][0]["type"] == "FACT" and "Non-finite" in inv["claims"][0]["text"]


def test_tools_respect_permissions(container) -> None:
    from model_forensic.ai.tools import Permission, ToolBox

    tb = ToolBox(container.service)
    out = tb.call("download_artifacts", {"run": "cand-radar-8f", "profile": "fast_debug"})
    assert "confirmation_required" in out
    tb2 = ToolBox(container.service, confirm=lambda name, args: True)
    assert "files" in tb2.call("download_artifacts", {"run": "cand-radar-8f", "profile": "fast_debug"})
    assert ToolBox(container.service, allow=frozenset({Permission.READ})).call("nope", {}).startswith('{"error"')
