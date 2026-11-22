"""`user:me` / "Mine": names from the config, case-insensitive, an actionable error when unknown."""

import pytest
from fastapi.testclient import TestClient

from model_forensic.api.app import create_app


def test_user_me_uses_configured_names_case_insensitively(container) -> None:
    container.settings.me = ["DANA", "dana@example.invalid"]
    res = container.service.search("user:me", limit=100)
    assert res.runs and {r.user for r in res.runs} == {"dana"}
    both = container.service.search("user:me status:completed", limit=100)
    assert both.runs and all(r.status.value == "completed" for r in both.runs)
    assert {r.user for r in container.service.search("user:DANA", limit=100).runs} == {"dana"}


def test_user_me_unknown_is_a_readable_400(container) -> None:
    container.settings.me = []
    container.service.me = list  # no az, no config
    with pytest.raises(ValueError, match="Who is 'me'"):
        container.service.search("user:me")
    r = TestClient(create_app(container)).get("/api/v1/runs", params={"q": "user:me"})
    assert r.status_code == 400 and "config/azure.local.yaml" in r.json()["error"]["message"]


def test_me_endpoint_lists_run_names(container) -> None:
    container.settings.me = ["sam"]
    assert TestClient(create_app(container)).get("/api/v1/me").json()["run_names"] == ["sam"]
