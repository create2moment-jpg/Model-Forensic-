"""The app says when the served UI build is older than the code (old screens after a git pull without a rebuild)."""

from __future__ import annotations

import json
from pathlib import Path

from model_forensic.api.app import BuildInfo


def test_stale_when_the_build_is_from_another_commit(tmp_path: Path) -> None:
    (tmp_path / "build.json").write_text(json.dumps({"rev": "abc1234"}))
    b = BuildInfo(tmp_path)
    b.source_rev = "abc1234"
    assert not b.stale
    b.source_rev = "def5678"
    assert b.stale
    (tmp_path / "build.json").write_text(json.dumps({"rev": "def5678+local"}))  # local edits on the same commit
    b2 = BuildInfo(tmp_path)
    b2.source_rev = "def5678"
    assert not b2.stale


def test_unknown_is_never_reported_as_stale(tmp_path: Path) -> None:
    b = BuildInfo(tmp_path)  # no build.json (an old build)
    b.source_rev = "abc1234"
    assert b.web_rev is None and not b.stale
    assert not BuildInfo(None).stale


def test_health_reports_both(container) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from model_forensic.api.app import create_app

    h = TestClient(create_app(container)).get("/api/v1/health").json()
    assert {"source_rev", "web_rev", "web_stale"} <= set(h)
