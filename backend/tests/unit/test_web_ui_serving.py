"""mf serve without a built UI explains itself (page, startup line, check) instead of a bare 404."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_missing_ui_shows_how_to_build_it(container, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api import app as appmod
    from model_forensic.application.config_checks import run

    monkeypatch.setattr(appmod, "web_dist_candidates", lambda: [tmp_path / "nowhere"])
    api = TestClient(appmod.create_app(container))
    r = api.get("/")
    assert r.status_code == 503 and "web UI is not built" in r.text and "npx vite build" in r.text
    assert str(tmp_path / "nowhere") in r.text
    assert api.get("/runs/x").status_code == 503  # any page route
    assert api.get("/api/v1/health").status_code == 200  # the API still answers
    assert api.get("/api/v1/nope").status_code == 404
    ui = {c.id: c for c in run(container)}["CFG-UI-1"]
    assert ui.status == "fail" and ui.severity == "critical" and "vite build" in ui.remediation


def test_ui_found_through_mf_web_dist(container, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from model_forensic.api import app as appmod

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>mf-ui</title>")
    monkeypatch.setenv("MF_WEB_DIST", str(dist))
    assert appmod.web_dist_candidates()[0] == dist
    api = TestClient(appmod.create_app(container))
    assert "mf-ui" in api.get("/").text and "mf-ui" in api.get("/runs/abc").text  # SPA routes
