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
