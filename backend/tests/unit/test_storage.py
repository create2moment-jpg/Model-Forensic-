"""Configurable storage: roles on their own paths, validation, overrides, worker suggestion — no source edits."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_forensic.application import storage
from model_forensic.container import build
from model_forensic.settings import Settings


def test_validate_path_cases(tmp_path: Path) -> None:
    ok = storage.validate_path(tmp_path / "new" / "artifacts", "artifacts")
    assert ok["ok"] and not ok["exists"] and ok["creatable"] and "will be created at start" in ok["notes"]
    assert not (tmp_path / "new").exists()  # validation never creates the folder
    f = tmp_path / "file"
    f.write_text("x")
    assert "not a folder" in storage.validate_path(f, "meta")["problems"][0]
    under_file = storage.validate_path(f / "sub", "meta")
    assert not under_file["ok"] and "is a file" in under_file["problems"][0]
    assert not storage.validate_path(tmp_path, "artifacts", min_free_gb=10**9)["ok"]  # not enough free space
    assert list(tmp_path.glob(".mf-write-probe-*")) == []  # the probe file is removed


def test_load_uses_role_paths_from_storage_overrides(
    tmp_path: Path, demo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "mf"
    (home / "config").mkdir(parents=True)
    (home / "model-forensic.yaml").write_text(
        f"data_dir: .mf\nbackends:\n  - {{kind: local, name: local, options: {{root: {demo_root}}}}}\n"
    )
    fast, bulk = tmp_path / "ssd", tmp_path / "hdd"
    monkeypatch.setenv("MF_HOME", str(home))
    monkeypatch.setenv("MF_AZURE_CONFIG", str(tmp_path / "none.yaml"))
    for k in ("MF_CONFIG", "MF_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    path = storage.save({"artifacts_dir": str(bulk / "artifacts"), "index_dir": str(fast), "download_workers": 6})
    assert path == home / "config/storage.local.yaml"
    s = Settings.load()
    assert s.paths()["artifacts"] == bulk / "artifacts" and s.paths()["index"] == fast
    assert s.paths()["meta"] == home / ".mf" / "meta" and s.download_workers == 6
    c = build(s)
    c.service.sync()
    assert (fast / "index.sqlite").is_file() and (fast / "forensic.sqlite").is_file()
    files = c.service.download("local:baseline-cl4f-s0")[0]
    assert files and all(str(f.local_path).startswith(str(bulk)) for f in files)
    storage.save({"artifacts_dir": None})  # clearing an override goes back to the default
    assert Settings.load().paths()["artifacts"] == home / ".mf" / "artifacts"


def test_worker_suggestion_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(storage, "usable_cpus", lambda: 64)
    monkeypatch.setattr(storage, "device_kind", lambda _p: "ssd")
    w = storage.suggest_workers(4, tmp_path)
    assert w["recommended"] == 16 and w["boost"] == 32 and "429" in w["boost_implications"]
    monkeypatch.setattr(storage, "device_kind", lambda _p: "hdd")
    assert storage.suggest_workers(4, tmp_path)["recommended"] == 4
    monkeypatch.setattr(storage, "usable_cpus", lambda: 1)
    assert storage.suggest_workers(4, tmp_path)["recommended"] == 2
    assert storage.usable_cpus() >= 1


def test_api_describe_validate_save(container, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from model_forensic.api.app import create_app

    monkeypatch.setenv("MF_HOME", str(tmp_path))
    api = TestClient(create_app(container))
    d = api.get("/api/v1/settings/storage").json()
    assert {r["role"] for r in d["roles"]} == {"artifacts", "meta", "index", "work"}
    assert any(f["path"] == "forensic.sqlite" and f["kind"] == "required" for f in d["files"])
    assert d["workers"]["recommended"] >= 2
    v = api.post("/api/v1/settings/storage/validate", json={"path": str(tmp_path / "x"), "role": "meta"}).json()
    assert v["ok"]
    bad = tmp_path / "afile"
    bad.write_text("x")
    assert api.put("/api/v1/settings/storage", json={"meta_dir": str(bad)}).status_code == 422
    r = api.put("/api/v1/settings/storage", json={"download_workers": 7}).json()
    assert container.service.artifact_store.workers == 7 and r["restart_required"] is False
    r = api.put("/api/v1/settings/storage", json={"work_dir": str(tmp_path / "books")}).json()
    assert r["restart_required"] is True and (tmp_path / "config/storage.local.yaml").is_file()
    assert api.put("/api/v1/settings/storage", json={"download_workers": 500}).status_code == 400
