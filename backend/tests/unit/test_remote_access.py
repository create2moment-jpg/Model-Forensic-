"""Opening Model Forensic from another machine: an SSH tunnel (the exact command is printed), or `mf serve --remote`
— every interface, nothing without the access token (local mode has no sign-in)."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from model_forensic.application import server_registry as reg
from model_forensic.container import Container

TOKEN = "s3cret-token-for-tests"


def _api(container: Container, token: str | None = TOKEN) -> TestClient:
    from model_forensic.api.app import create_app

    container.settings.server.access_token = token
    return TestClient(create_app(container), follow_redirects=False)


def test_nothing_without_the_token(container: Container) -> None:
    api = _api(container)
    assert api.get("/api/v1/health").status_code == 200  # mf status can still tell it is up
    r = api.get("/api/v1/backends")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    page = api.get("/runs")
    assert page.status_code == 401 and "Access token needed" in page.text
    assert api.get("/api/v1/backends", headers={"X-MF-Token": "wrong"}).status_code == 401
    assert api.get("/api/v1/backends", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert api.get("/api/v1/backends", headers={"X-MF-Token": TOKEN}).status_code == 200


def test_the_link_signs_the_browser_in_and_leaves_the_url(container: Container) -> None:
    api = _api(container)
    r = api.get(f"/runs?q=exp%3Astarred&token={TOKEN}")
    assert r.status_code == 303 and r.headers["location"] == "/runs?q=exp%3Astarred"
    cookie = r.headers["set-cookie"]
    assert "mf_token=" in cookie and "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    api.cookies.set("mf_token", TOKEN)
    assert api.get("/api/v1/backends").status_code == 200
    assert api.get("/runs").status_code != 401


def test_without_a_token_nothing_changes(container: Container) -> None:
    api = _api(container, None)
    assert api.get("/api/v1/backends").status_code == 200


def test_ssh_hint_fills_in_user_and_address() -> None:
    env = {"SSH_CONNECTION": "192.0.2.10 52311 198.51.100.7 22", "USER": "dana"}
    assert reg.ssh_tunnel_hint(8765, env) == "ssh -N -L 8765:127.0.0.1:8765 dana@198.51.100.7"
    v6 = {"SSH_CONNECTION": "2001:db8::1 52311 2001:db8::7 22", "USER": "dana"}
    assert reg.ssh_tunnel_hint(9000, v6) == "ssh -N -L 9000:127.0.0.1:9000 dana@[2001:db8::7]"
    assert reg.ssh_tunnel_hint(8765, {}) is None


def test_the_link_is_kept_for_you_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MF_HOME", str(tmp_path))
    p = reg.register("0.0.0.0", 8799, tmp_path, [f"http://host:8799/?token={TOKEN}"])
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert json.loads(p.read_text())["open_links"] == [f"http://host:8799/?token={TOKEN}"]


def test_serve_remote_requires_a_token_and_prints_the_link(
    container: Container, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn
    from typer.testing import CliRunner

    from model_forensic.cli import main as cli

    monkeypatch.setenv("MF_HOME", str(tmp_path))
    monkeypatch.setattr(cli.base, "make_container", lambda _cfg=None: container)  # type: ignore[attr-defined]
    monkeypatch.setattr(reg, "port_in_use", lambda *_a: False)
    monkeypatch.setattr(reg, "lan_addresses", lambda: ["devbox.example"])
    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: seen.update(host=host, port=port))
    res = CliRunner().invoke(cli.app, ["serve", "--remote", "--port", "8811", "--token", TOKEN])
    assert res.exit_code == 0, res.output
    assert seen == {"host": "0.0.0.0", "port": 8811}
    assert container.settings.server.access_token == TOKEN
    assert f"http://devbox.example:8811/?token={TOKEN}" in res.output
    # without --token a random one is made; on loopback none is needed
    container.settings.server.access_token = None
    res = CliRunner().invoke(cli.app, ["serve", "--remote", "--port", "8812"])
    assert res.exit_code == 0 and len(container.settings.server.access_token or "") >= 24
    container.settings.server.access_token = None
    res = CliRunner().invoke(cli.app, ["serve", "--port", "8813"])
    assert res.exit_code == 0 and container.settings.server.access_token is None
