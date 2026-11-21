"""API hardening: typed errors without stack traces, deep health, connection test job, diagnosis on failures."""

import time

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app


def _client(container) -> TestClient:
    return TestClient(create_app(container), raise_server_exceptions=False)


def test_unexpected_error_is_typed_without_trace(container, monkeypatch) -> None:
    c = _client(container)

    def boom(*a, **k):
        raise RuntimeError("secret internal detail\nTraceback ...")

    monkeypatch.setattr(container.service, "search", boom)
    r = c.get("/api/v1/runs", params={"q": "x"})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "internal" and err["details"]["request_id"]
    assert "Traceback" not in r.text and "secret internal detail" not in r.text


def test_value_error_is_400(container, monkeypatch) -> None:
    c = _client(container)

    def bad(*a, **k):
        raise ValueError("no completed runs indexed — run a sync first")

    monkeypatch.setattr("model_forensic.api.app.check_run", bad, raising=False)
    monkeypatch.setattr(container.service, "search", bad)
    r = c.get("/api/v1/runs", params={"q": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"


def test_health_deep(container) -> None:
    c = _client(container)
    d = c.get("/api/v1/health/deep").json()
    assert d["status"] in ("ok", "degraded")
    assert d["backends"][0]["ok"] and d["backends"][0]["diagnosis"] is None
    assert "free_gb" in d["disk"] and d["uptime_s"] >= 0


def test_health_deep_shows_diagnosis_of_failed_backend(container) -> None:
    container.service.sync_errors["local"] = "ClientAuthenticationError: AADSTS700082: refresh token has expired"
    c = _client(container)
    d = c.get("/api/v1/health/deep").json()
    b = next(x for x in d["backends"] if x["name"] == "local")
    assert not b["ok"] and b["diagnosis"]["cause"] == "token_expired"
    assert d["status"] == "down"
    assert c.get("/api/v1/backends").json()[0]["diagnosis"]["cause"] == "token_expired"


def test_backend_check_job(container) -> None:
    c = _client(container)
    assert c.post("/api/v1/backends/nope/check").status_code == 404
    jid = c.post("/api/v1/backends/local/check").json()["job_id"]
    for _ in range(100):
        j = c.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done" and j["result"]["ok"] and j["result"]["checks"][0]["name"] == "list_runs"


def test_m4_keeps_spikes_and_nan() -> None:
    import numpy as np

    from model_forensic.shared.downsample import m4_indices

    v = np.linspace(1.0, 0.1, 100_000)
    v[31_337] = 50.0  # one spike
    v[77_777] = np.nan  # one NaN
    idx = m4_indices(v, 2000)
    assert len(idx) <= 2000 + 500
    assert 31_337 in idx and 77_777 in idx and 0 in idx and 99_999 in idx
    assert list(m4_indices(v[:100], 2000)) == list(range(100))


def test_metrics_endpoint_reports_totals(container) -> None:
    c = _client(container)
    run = "local:xf-pipe-finetune"
    keys = c.get(f"/api/v1/runs/{run}").json()["metric_keys"][:1]
    s = c.get(f"/api/v1/runs/{run}/metrics", params={"keys": keys, "max_points": 50}).json()[0]
    assert s["points_total"] >= len(s["values"]) and "non_finite" in s


def test_ttl_cache_expires(monkeypatch) -> None:
    from model_forensic.application import service as mod

    now = [100.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    c = mod._LRU(4)
    c.put("run", 1, ttl_s=30)
    c.put("done", 2)
    assert c.get("run") == 1
    now[0] += 31
    assert c.get("run") is None and c.get("done") == 2


def test_finding_explain_and_feedback(container) -> None:
    c = _client(container)
    res = c.post("/api/v1/runs/local:lr-sweep-1e3/analyze", json={}).json()
    findings = res["findings"] if isinstance(res, dict) and "findings" in res else None
    if findings is None:  # job-based analyze
        fs = container.service.analyze("local:lr-sweep-1e3", None, persist=True)
        findings = [f.model_dump(mode="json") for r in fs for f in r.findings]
    fid = findings[0]["id"]
    ex = c.get(f"/api/v1/findings/{fid}/explain").json()
    assert ex["why"] and "confidence" in ex["why"][0]
    assert c.put(f"/api/v1/findings/{fid}/feedback", json={"verdict": "nope"}).status_code == 400
    assert c.put(f"/api/v1/findings/{fid}/feedback", json={"verdict": "wrong", "note": "LR warm-up"}).status_code == 200
    rep = c.get("/api/v1/feedback/report").json()
    row = next(a for a in rep["analyses"] if a["wrong"] == 1)
    assert row["false_alarm_rate"] == 1.0 and row["examples"][0]["note"] == "LR warm-up"
