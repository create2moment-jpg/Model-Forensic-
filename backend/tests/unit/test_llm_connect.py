"""One-click local LLM: discover the model on 127.0.0.1, test it, switch only if it answers, remember it."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_forensic.container import build
from model_forensic.settings import BackendConfig, Settings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _Handler(BaseHTTPRequestHandler):
    chat_ok = True

    def log_message(self, *_a: object) -> None:
        pass

    def _send(self, code: int, body: dict[str, object]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": "qwen2.5-14b-instruct-q4", "object": "model"}]})
        else:
            self._send(404, {"error": "nope"})

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        if self.path == "/v1/chat/completions" and type(self).chat_ok:
            self._send(
                200,
                {
                    "id": "x",
                    "model": "qwen2.5-14b-instruct-q4",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
            )
        else:
            self._send(500, {"error": {"message": "model failed to load"}})


@pytest.fixture()
def local_server() -> Iterator[str]:
    port = _free_port()
    srv = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Handler.chat_ok = True
    yield f"127.0.0.1:{port}/v1"  # scheme-less, as people type it
    srv.shutdown()


def _container(data_dir: Path, demo_root: Path):  # type: ignore[no-untyped-def]
    return build(
        Settings(
            data_dir=data_dir,
            backends=[BackendConfig(kind="local", name="local", options={"root": str(demo_root)})],
            artifact_min_free_gb=0.0,
            knowledge_dir=data_dir / "knowledge",
        )
    )


def test_connect_switches_pings_and_survives_a_restart(tmp_path: Path, demo_root: Path, local_server: str) -> None:
    from model_forensic.api.app import create_app

    c = _container(tmp_path / ".mf", demo_root)
    api = TestClient(create_app(c))
    h = api.get("/api/v1/health").json()
    assert h["llm_enabled"] is False and h["llm_status"] is None
    assert api.post("/api/v1/settings/llm/ping").json()["state"] == "offline"
    r = api.post("/api/v1/settings/llm/connect-local", json={"base_url": local_server}).json()
    assert r["ok"] and r["model"] == "qwen2.5-14b-instruct-q4" and r["base_url"].startswith("http://127.0.0.1:")
    h = api.get("/api/v1/health").json()
    assert h["llm"] == "qwen2.5-14b-instruct-q4" and h["llm_enabled"] and h["llm_status"]["state"] == "ok"
    assert api.post("/api/v1/settings/llm/ping").json()["state"] == "ok"
    saved = json.loads((tmp_path / ".mf" / "llm.json").read_text())
    from model_forensic.settings import LLMConfig

    assert saved["enabled"] and saved["model"] == "qwen2.5-14b-instruct-q4"
    assert set(saved) == set(LLMConfig.model_fields)  # the config only — a key is never written
    # a restarted server comes back with the working model
    c2 = _container(tmp_path / ".mf", demo_root)
    assert c2.settings.llm.enabled and c2.llm.model == "qwen2.5-14b-instruct-q4"
    # the server breaks: the status says so, the configuration is kept
    _Handler.chat_ok = False
    p = api.post("/api/v1/settings/llm/ping").json()
    assert p["state"] == "failed" and p["error"]
    assert api.get("/api/v1/health").json()["llm_status"]["state"] == "failed"


def test_no_switch_when_nothing_answers_or_the_chat_fails(tmp_path: Path, demo_root: Path, local_server: str) -> None:
    from model_forensic.api.app import create_app

    c = _container(tmp_path / ".mf", demo_root)
    api = TestClient(create_app(c))
    r = api.post("/api/v1/settings/llm/connect-local", json={"base_url": f"127.0.0.1:{_free_port()}/v1"}).json()
    assert not r["ok"] and "No server answers" in r["error"] and "llama-server" in r["error"]
    _Handler.chat_ok = False
    r = api.post("/api/v1/settings/llm/connect-local", json={"base_url": local_server}).json()
    assert not r["ok"] and "test chat failed" in r["error"] and r["models"] == ["qwen2.5-14b-instruct-q4"]
    assert c.settings.llm.enabled is False and not (tmp_path / ".mf" / "llm.json").exists()  # nothing changed


def test_unreadable_saved_settings_fall_back(tmp_path: Path, demo_root: Path) -> None:
    (tmp_path / ".mf").mkdir()
    (tmp_path / ".mf" / "llm.json").write_text("{ not json")
    c = _container(tmp_path / ".mf", demo_root)
    assert c.settings.llm.enabled is False  # model-forensic.yaml / defaults
