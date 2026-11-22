"""Shared deployment: proxy-header and OIDC auth, admin-only settings, authors, scheduler alerts, backups."""

import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.api.auth import Authenticator, AuthError
from model_forensic.application.scheduler import Scheduler
from model_forensic.settings import ServerConfig


def shared(container, **kw):
    container.settings.server = ServerConfig(
        mode="shared", auth="header", allowed_domains=["example.com"], admins=["boss@example.com"], **kw
    )
    return TestClient(create_app(container))


def test_header_auth_and_admin_settings(container) -> None:
    c = shared(container)
    assert c.get("/api/v1/health").status_code == 200  # open path
    assert c.get("/api/v1/me").status_code == 401
    assert c.get("/api/v1/me", headers={"X-Forwarded-Email": "eve@evil.com"}).status_code == 403
    me = c.get("/api/v1/me", headers={"X-Forwarded-Email": "ana@example.com"}).json()
    assert me == {
        "id": "ana@example.com",
        "name": "ana@example.com",
        "admin": False,
        "mode": "shared",
        "run_names": ["ana@example.com"],
    }
    r = c.put("/api/v1/settings/azure", json={"workspaces": []}, headers={"X-Forwarded-Email": "ana@example.com"})
    assert r.status_code == 403
    assert c.get("/api/v1/me", headers={"X-Forwarded-Email": "Boss@Example.com"}).json()["admin"] is True


def test_authors_recorded(container) -> None:
    c = shared(container)
    h = {"X-Forwarded-Email": "ana@example.com"}
    st = c.post("/api/v1/studies", json={"title": "shared", "runs": ["local:baseline-cl4f-s0"]}, headers=h).json()
    assert st["owner"] == "ana@example.com"
    st = c.post(
        f"/api/v1/studies/{st['id']}/notes",
        json={"text": "looked at it"},
        headers={"X-Forwarded-Email": "bo@example.com"},
    ).json()
    assert st["notes"][-1]["author"] == "bo@example.com"
    assert st["timeline"][-1]["actor"] == "bo@example.com"


def test_oidc_token_validation() -> None:
    jwt = pytest.importorskip("jwt")
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cfg = ServerConfig(
        mode="shared",
        auth="oidc",
        oidc_issuer="https://login.example/t/v2.0",
        oidc_audience="api://mf",
        allowed_domains=["example.com"],
    )
    a = Authenticator(cfg)

    class Jwks:
        def get_signing_key_from_jwt(self, _t):
            return type("K", (), {"key": key.public_key()})()

    a._jwks, a._jwks_at = Jwks(), time.time()
    now = int(time.time())
    good = jwt.encode(
        {
            "iss": cfg.oidc_issuer,
            "aud": "api://mf",
            "exp": now + 60,
            "preferred_username": "ana@example.com",
            "name": "Ana",
        },
        key,
        algorithm="RS256",
    )
    u = a.user({"authorization": f"Bearer {good}"})
    assert (u.id, u.name) == ("ana@example.com", "Ana")
    bad_aud = jwt.encode(
        {"iss": cfg.oidc_issuer, "aud": "other", "exp": now + 60, "preferred_username": "a@example.com"},
        key,
        algorithm="RS256",
    )
    expired = jwt.encode(
        {"iss": cfg.oidc_issuer, "aud": "api://mf", "exp": now - 60, "preferred_username": "a@example.com"},
        key,
        algorithm="RS256",
    )
    for tok in (bad_aud, expired, "garbage"):
        with pytest.raises(AuthError):
            a.user({"authorization": f"Bearer {tok}"})
    with pytest.raises(AuthError):
        a.user({})


def test_scheduler_alerts_and_nightly_books(container, monkeypatch) -> None:
    st = container.studies.create("nightly", runs=["local:baseline-cl4f-s0"])
    container.settings.server = ServerConfig(
        mode="shared", sync_minutes=5, nightly_books_utc="02:00", book_studies=[st.id]
    )
    sch = Scheduler(container)
    t0 = datetime(2026, 11, 21, 1, 0, tzinfo=timezone.utc)
    first = sch.tick(t0)
    assert first["alerts"] == 0 and first["books"] == [] and sch.seen_failed  # first scan only learns
    sch.seen_failed.discard(next(iter(sch.seen_failed)))  # pretend one failure is new
    second = sch.tick(t0 + timedelta(hours=2))
    assert second["alerts"] == 1 and sch.notifier.sent[0]["title"] == "1 new failed run(s)"
    assert second["books"] == [st.id] and sch.tick(t0 + timedelta(hours=3))["books"] == []  # once per day
    c = TestClient(create_app(container))
    assert c.get("/api/v1/scheduler").json()["sync_minutes"] == 5


def test_backup(container, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from model_forensic.cli.main import app

    cfg = tmp_path / "mf.yaml"
    cfg.write_text(f"data_dir: {container.settings.data_dir}\n")
    out = tmp_path / "b.tar.gz"
    r = CliRunner().invoke(app, ["backup", "--out", str(out), "--config", str(cfg)])
    assert r.exit_code == 0, r.stdout
    with tarfile.open(out) as tf:
        names = tf.getnames()
    assert "data/forensic.sqlite" in names and "data/index.sqlite" in names
