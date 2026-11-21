"""Endpoints used by the web UI: jobs/progress, browser downloads, favorites, BYOK settings, SPA serving."""

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from fastapi.testclient import TestClient

from model_forensic.api.app import create_app


def wait_job(c: TestClient, job_id: str) -> dict:
    for _ in range(100):
        j = c.get(f"/api/v1/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_download_job_reports_progress_and_files(container) -> None:
    c = TestClient(create_app(container))
    jid = c.post("/api/v1/runs/cand-radar-8f/download", json={"profile": "fast_debug"}).json()["job_id"]
    j = wait_job(c, jid)
    assert j["status"] == "done" and j["progress"]["done_files"] == j["progress"]["total_files"] > 0
    assert any(f["path"] == "config.yaml" for f in j["result"]["files"])
    assert any(x["id"] == jid for x in c.get("/api/v1/jobs").json())
    # selective download of explicit paths
    jid = c.post("/api/v1/runs/cand-radar-8f/download", json={"profile": "custom", "paths": ["run.json"]}).json()[
        "job_id"
    ]
    assert [f["path"] for f in wait_job(c, jid)["result"]["files"]] == ["run.json"]


def test_artifact_file_download_and_traversal(container) -> None:
    c = TestClient(create_app(container))
    r = c.get("/api/v1/runs/cand-radar-8f/artifacts/file", params={"path": "outputs/eval_results.json"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "NDS" in r.json()
    assert c.get("/api/v1/runs/cand-radar-8f/artifacts/file", params={"path": "../../etc/passwd"}).status_code == 404


def test_favorites_flag(container) -> None:
    c = TestClient(create_app(container))
    c.put("/api/v1/runs/local:cand-uncert/favorite", params={"value": True})
    runs = {r["name"]: r["favorite"] for r in c.get("/api/v1/runs").json()["runs"]}
    assert runs["cand-uncert"] is True and runs["cand-radar-8f"] is False


class _FakeLLM(BaseHTTPRequestHandler):
    seen_auth: list[str] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeLLM.seen_auth.append(self.headers.get("Authorization", ""))
        out = json.dumps({"model": body["model"], "choices": [{"message": {"content": "OK"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a: object) -> None:
        pass


def test_byok_settings_roundtrip_never_returns_key(container) -> None:
    srv = HTTPServer(("127.0.0.1", 0), _FakeLLM)
    Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = TestClient(create_app(container))
        s = c.put(
            "/api/v1/settings/llm",
            json={
                "enabled": True,
                "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                "model": "m1",
                "api_key": "sk-session-key-123456",
            },
        ).json()
        assert s["has_session_key"] and s["active_model"] == "m1"
        assert "sk-session-key" not in json.dumps(s) and "sk-session-key" not in json.dumps(
            c.get("/api/v1/settings/llm").json()
        )
        t = c.post("/api/v1/settings/llm/test").json()
        assert t["ok"] and t["reply"] == "OK" and _FakeLLM.seen_auth[-1] == "Bearer sk-session-key-123456"
        # key=None keeps it, "" clears it
        assert c.put("/api/v1/settings/llm", json={"enabled": True, "base_url": "http://x/v1", "model": "m2"}).json()[
            "has_session_key"
        ]
        assert not c.put(
            "/api/v1/settings/llm", json={"enabled": False, "base_url": "http://x/v1", "model": "m2", "api_key": ""}
        ).json()["has_session_key"]
        assert c.get("/api/v1/health").json()["llm"].startswith("offline")
        audit = c.get("/api/v1/diagnostics").json()["audit"]
        assert any(a["action"] == "llm_settings" for a in audit) and "sk-session" not in json.dumps(audit)
    finally:
        srv.shutdown()


def test_spa_is_served_when_built(container, tmp_path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>MF</html>")
    monkeypatch.setenv("MF_WEB_DIST", str(dist))
    c = TestClient(create_app(container))
    assert c.get("/runs/local:cand-radar-8f").text == "<html>MF</html>"  # client-side route
    assert c.get("/api/v1/health").json()["ok"]
    assert c.get("/api/v1/nope").status_code == 404
