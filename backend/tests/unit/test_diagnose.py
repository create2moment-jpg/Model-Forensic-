"""Connection diagnosis: one realistic message per cause, preflight and redaction."""

import json

import pytest
from typer.testing import CliRunner

from model_forensic.infrastructure.connectors.azureml.diagnose import CAUSES, RULES, classify, preflight, redact

EXAMPLES = {
    "not_installed": "BackendUnavailableError: Azure ML support not installed: pip install 'model-forensic[azure]'",
    "tls_interception": "ServiceRequestError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "self-signed certificate in certificate chain",
    "proxy": "ServiceRequestError: ProxyError('Cannot connect to proxy.', OSError('Tunnel connection failed: 407'))",
    "dns": "ServiceRequestError: <urllib3.connection.HTTPSConnection>: Failed to resolve 'management.azure.com' "
    "([Errno -2] Name or service not known)",
    "token_expired": "ClientAuthenticationError: AADSTS700082: The refresh token has expired due to inactivity.",
    "not_logged_in": "CredentialUnavailableError: Please run 'az login' to set up an account",
    "wrong_tenant": "HttpResponseError: (InvalidAuthenticationTokenTenant) The access token is from the wrong issuer",
    "subscription_not_found": "ResourceNotFoundError: (SubscriptionNotFound) The subscription 'x' could not be found.",
    "workspace_not_found": "ResourceNotFoundError: (ResourceGroupNotFound) Resource group 'rg' could not be found.",
    "no_permission": "HttpResponseError: (AuthorizationFailed) The client 'a' does not have authorization to perform",
    "managed_identity_unavailable": "CredentialUnavailableError: ManagedIdentityCredential authentication unavailable",
    "network_timeout": "ServiceResponseError: Connection aborted, ReadTimeout: read timed out",
    "throttled": "HttpResponseError: (429) TooManyRequests",
    "config_invalid": "KeyError: 'subscription_id'",
}


@pytest.mark.parametrize(("cause", "message"), sorted(EXAMPLES.items()))
def test_each_cause_has_an_example(cause: str, message: str) -> None:
    d = classify(message)
    assert d.cause == cause, (message, d)
    assert d.fixes and d.summary and d.matched


def test_every_rule_is_covered_and_known() -> None:
    assert {r.cause for r in RULES} == set(EXAMPLES)
    assert set(EXAMPLES) | {"unknown"} == set(CAUSES)


def test_unknown_points_to_docs() -> None:
    d = classify("Something odd happened")
    assert d.cause == "unknown" and any("troubleshooting" in f for f in d.fixes)


def test_redact_removes_ids_emails_and_sas() -> None:
    t = redact("sub 0f8fad5b-d9cb-469f-a165-70867728950e user a.b@corp.example url ?sv=2023&sig=abc%2Fdef&se=x")
    assert "0f8fad5b" not in t and "corp.example" not in t and "abc%2F" not in t and "<guid>" in t


def test_preflight_flags_placeholders_and_hides_proxy_password(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.local:8080")
    monkeypatch.setattr("shutil.which", lambda _: None)
    pre = preflight([{"name": "ws", "subscription_id": "<subscription-id>", "resource_group": "rg"}])
    by = {p.name: p for p in pre}
    assert not by["workspace ws"].ok and "workspace" in by["workspace ws"].detail
    assert "secret" not in by["network environment"].detail
    assert not by["azure cli"].ok


def test_doctor_live_json_and_bundle(tmp_path, monkeypatch) -> None:
    from model_forensic.cli.main import app

    cfg = tmp_path / "mf.yaml"
    cfg.write_text(
        f"data_dir: {tmp_path / 'd'}\nbackends:\n  - kind: azureml\n    name: ws1\n    options:\n"
        "      subscription_id: 0f8fad5b-d9cb-469f-a165-70867728950e\n      resource_group: rg\n      workspace: w\n"
    )

    def boom(kind, opts):
        raise RuntimeError("ClientAuthenticationError: AADSTS700082: The refresh token has expired")

    monkeypatch.setattr("model_forensic.infrastructure.connectors.create_connector", boom)
    monkeypatch.setattr("shutil.which", lambda _: None)
    out = tmp_path / "support.json"
    res = CliRunner().invoke(app, ["doctor", "--live", "--json", "--bundle", str(out), "--config", str(cfg)])
    assert res.exit_code == 1
    doc = json.loads(res.stdout)
    assert doc["workspaces"]["ws1"][0]["diagnosis"]["cause"] == "token_expired"
    assert "0f8fad5b" not in out.read_text()
