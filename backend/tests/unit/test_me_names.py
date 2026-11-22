"""Who is "me": an explicit, editable list of names (exact match) — persisted without touching anything else."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


def test_me_names_edit_persist_and_filter(container, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from model_forensic.api.app import create_app

    cfg = tmp_path / "azure.local.yaml"
    cfg.write_text(
        "defaults: {tenant_id: t-1}\nworkspaces:\n  - {name: ws1, subscription_id: s, resource_group: g, workspace: w}\n"
    )
    monkeypatch.setenv("MF_AZURE_CONFIG", str(cfg))
    monkeypatch.setenv("MF_AZURE_SUBSCRIPTION_ID", "env-sub")  # an env workspace must never be written to the file
    monkeypatch.setenv("MF_AZURE_RESOURCE_GROUP", "env-rg")
    monkeypatch.setenv("MF_AZURE_WORKSPACE", "env-ws")
    container.settings.me = []
    api = TestClient(create_app(container))
    assert api.get("/api/v1/me").json()["id"]  # the identity endpoint is untouched
    r = api.put("/api/v1/me/names", json={"names": ["dana", " Dana ", "dana@example.org"]}).json()
    assert r["names"] == ["dana", "Dana", "dana@example.org"] and r["editable"]
    doc = yaml.safe_load(cfg.read_text())
    assert doc["defaults"] == {"tenant_id": "t-1", "me": ["dana", "Dana", "dana@example.org"]}
    assert [w["name"] for w in doc["workspaces"]] == ["ws1"]
    mine = api.get("/api/v1/runs", params={"q": "user:me", "limit": 500}).json()
    assert mine["total"] > 0 and {x["user"] for x in mine["runs"]} == {"dana"}  # exact, case-insensitive
    assert api.put("/api/v1/me/names", json={"names": [f"n{i}" for i in range(25)]}).status_code == 422
    cleared = api.put("/api/v1/me/names", json={"names": []}).json()
    assert cleared["names"] == [] and cleared["source"] == "not set"  # nothing guessed: no az login here
