"""The site interface: local Azure config file / env, az CLI detection, and the Settings API that writes it."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_forensic.api.app import create_app
from model_forensic.interface import azure
from model_forensic.settings import Settings

SUB = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config" / "azure.local.yaml"
    monkeypatch.setenv("MF_AZURE_CONFIG", str(path))
    for k in ("SUBSCRIPTION_ID", "RESOURCE_GROUP", "WORKSPACE", "WORKSPACE_TITLE", "TENANT_ID"):
        monkeypatch.delenv(f"MF_AZURE_{k}", raising=False)
    return path


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_template_placeholders_are_skipped(_isolated: Path) -> None:
    write(_isolated, Path(__file__).parents[3].joinpath("config/azure.example.yaml").read_text())
    iface = azure.load()
    assert iface.placeholders() == ["my-workspace"] and iface.to_backends() == []
    s = Settings.load(Path("/nonexistent.yaml"))
    assert any("fill in my-workspace" in n for n in s.notices)
    assert [b.kind for b in s.backends] == ["local"]  # falls back to demo data


def test_filled_file_becomes_backends(_isolated: Path) -> None:
    write(
        _isolated,
        f"""
defaults: {{credential: cli, tenant_id: tid}}
workspaces:
  - {{name: research, subscription_id: {SUB}, resource_group: rg-a, workspace: ws-research}}
  - {{name: prod, title: Prod, subscription_id: {SUB}, resource_group: rg-a, workspace: ws-prod, default: true,
      credential: managed_identity}}
""",
    )
    backends = azure.load().to_backends()
    assert [b.name for b in backends] == ["prod", "research"]  # default first
    assert backends[0].options["credential"] == "managed_identity" and backends[1].options["tenant_id"] == "tid"
    assert backends[1].title == "ws-research" and backends[0].options["metrics_api"] == "runhistory"
    s = Settings.load(Path("/nonexistent.yaml"))
    assert [b.name for b in s.backends] == ["prod", "research"] and not s.notices


def test_env_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MF_AZURE_SUBSCRIPTION_ID", SUB)
    monkeypatch.setenv("MF_AZURE_RESOURCE_GROUP", "rg")
    monkeypatch.setenv("MF_AZURE_WORKSPACE", "My_WS")
    iface = azure.load()
    assert iface.source == "env" and iface.workspaces[0].name == "my_ws"
    assert iface.to_backends()[0].options["workspace"] == "My_WS"


def test_invalid_and_duplicate(_isolated: Path) -> None:
    write(_isolated, "workspaces:\n  - {name: 'Bad Name', subscription_id: s, resource_group: r, workspace: w}\n")
    with pytest.raises(azure.InterfaceError):
        azure.load()
    write(
        _isolated,
        f"workspaces:\n  - {{name: a, subscription_id: {SUB}, resource_group: r, workspace: w}}\n"
        f"  - {{name: a, subscription_id: {SUB}, resource_group: r, workspace: w2}}\n",
    )
    with pytest.raises(azure.InterfaceError):
        azure.load()


def fake_az(args: list[str]):
    if args[:2] == ["account", "show"]:
        return {"id": SUB, "name": "Perception", "tenantId": "tid", "user": {"name": "me@example.com"}}
    if args[:2] == ["resource", "list"]:
        return [{"workspace": "WS-Prod", "resource_group": "rg-a", "location": "westeurope"}]
    if args[:3] == ["ad", "signed-in-user", "show"]:
        return {"name": "Me", "mail": "me@example.com", "upn": "me@example.com"}
    raise AssertionError(args)


def test_detect_and_save_roundtrip(monkeypatch: pytest.MonkeyPatch, _isolated: Path) -> None:
    monkeypatch.setattr(azure, "run_az", fake_az)
    found = azure.detect()
    assert found["workspaces"][0]["name"] == "ws-prod" and found["user"]["name"] == "Me"
    with pytest.raises(azure.InterfaceError):
        azure.detect("not-a-guid")
    iface = azure.AzureInterface(
        workspaces=[azure.AzureWorkspace(**{k: v for k, v in found["workspaces"][0].items() if k != "location"})]
    )
    azure.save(iface)
    assert azure.load().ready()[0].workspace == "WS-Prod" and _isolated.read_text().startswith("# Local Azure ML")


def test_settings_api_detect_save_reload(container, monkeypatch: pytest.MonkeyPatch, _isolated: Path) -> None:
    monkeypatch.setattr(azure, "run_az", fake_az)
    c = TestClient(create_app(container))
    assert c.get("/api/v1/settings/azure").json()["workspaces"] == []
    det = c.post("/api/v1/settings/azure/detect").json()
    body = {
        "defaults": {"credential": "cli"},
        "workspaces": [
            {
                "name": "ws-prod",
                "subscription_id": SUB,
                "resource_group": "rg-a",
                "workspace": "WS-Prod",
                "default": True,
            }
        ],
    }
    out = c.put("/api/v1/settings/azure", json=body).json()
    assert det["subscription_id"] == SUB and out["workspaces"][0]["studio_url"].startswith("https://ml.azure.com/")
    assert _isolated.exists()
    names = [b["name"] for b in c.get("/api/v1/backends").json()]
    assert names[0] == "ws-prod" and "local" in names  # the Azure workspace is listed (unavailable without the SDK)
    assert c.put("/api/v1/settings/azure", json={"workspaces": [{"name": "x"}]}).status_code == 422
