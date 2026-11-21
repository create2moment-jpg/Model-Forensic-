"""Turn Azure connection failures into a cause and concrete fixes.

Used by ``mf doctor --live``: every failed check gets a ``cause`` (one of :data:`CAUSES`) and ordered ``fixes``.
The rules are plain substring/regex matches over the exception text, so a person — or a local coding LLM reading
``mf doctor --live --json`` — can see exactly why a cause was chosen (``matched``). Add a rule here when a new
failure mode shows up; ``tests/unit/test_diagnose.py`` holds one example message per rule.

The preflight (:func:`preflight`) runs without network calls to Azure: packages, Azure CLI, proxy and CA bundle
environment, and the shape of each configured workspace.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CAUSES = (
    "not_installed",
    "not_logged_in",
    "token_expired",
    "wrong_tenant",
    "subscription_not_found",
    "workspace_not_found",
    "no_permission",
    "managed_identity_unavailable",
    "tls_interception",
    "proxy",
    "dns",
    "network_timeout",
    "throttled",
    "config_invalid",
    "unknown",
)


@dataclass(frozen=True)
class Rule:
    cause: str
    pattern: str
    summary: str
    fixes: tuple[str, ...]


RULES: tuple[Rule, ...] = (
    Rule(
        "not_installed",
        r"No module named 'azure|Azure ML support not installed|ModuleNotFoundError.*azure",
        "The Azure SDK packages are not installed in this Python environment.",
        ("pip install 'model-forensic[azure]'  (inside the same venv as `mf`)",),
    ),
    Rule(
        "tls_interception",
        r"CERTIFICATE_VERIFY_FAILED|self[- ]signed certificate|unable to get local issuer certificate|SSLError",
        "A corporate proxy re-signs TLS traffic and Python does not trust its certificate.",
        (
            "export REQUESTS_CA_BUNDLE=/path/to/corporate-ca-bundle.pem  (and SSL_CERT_FILE to the same file)",
            "Docker: put the CA .crt files in deploy/ca/ and rebuild (see deploy/ca/README.md)",
            "pip install pip-system-certs  (uses the operating system's trust store)",
        ),
    ),
    Rule(
        "proxy",
        r"ProxyError|407 Proxy Authentication|Tunnel connection failed|Cannot connect to proxy",
        "The HTTP(S) proxy refused or could not be reached.",
        (
            "check HTTPS_PROXY / HTTP_PROXY / NO_PROXY in this shell (mf doctor --live prints them)",
            "if the proxy needs authentication, use http://user:password@proxy:port in HTTPS_PROXY",
        ),
    ),
    Rule(
        "dns",
        r"Name or service not known|getaddrinfo failed|nodename nor servname|Temporary failure in name resolution|"
        r"NameResolutionError|Failed to resolve",
        "A host name could not be resolved (offline, VPN off, or private-endpoint DNS missing).",
        (
            "connect the VPN if the workspace uses private endpoints",
            "nslookup management.azure.com  and  nslookup <region>.api.azureml.ms",
        ),
    ),
    Rule(
        "token_expired",
        r"AADSTS700082|AADSTS50173|refresh token has expired|expired due to inactivity|AADSTS70043|"
        r"Interactive authentication is needed|run 'az login' to setup account|az login --scope",
        "The Azure CLI login has expired.",
        ("az login", "az account set --subscription <subscription id from config/azure.local.yaml>"),
    ),
    Rule(
        "not_logged_in",
        r"Please run 'az login'|AzureCliCredential.*(not installed|Azure CLI not found)|"
        r"DefaultAzureCredential failed to retrieve a token|No accounts were found|ChainedTokenCredential failed",
        "No Azure credential is available (not logged in, or the Azure CLI is not on PATH).",
        (
            "az login   (install the Azure CLI first if `az` is not found)",
            "or set `credential: managed_identity` in config/azure.local.yaml when running inside Azure",
            "or set `tenant_id` in config/azure.local.yaml if you have several tenants",
        ),
    ),
    Rule(
        "wrong_tenant",
        r"AADSTS50020|AADSTS90002|AADSTS700016|InvalidAuthenticationTokenTenant|does not exist in tenant|"
        r"not found in the directory|token is from the wrong issuer",
        "The token was issued by a different tenant than the subscription belongs to.",
        (
            "set `tenant_id` for this workspace in config/azure.local.yaml",
            "az login --tenant <tenant id>",
        ),
    ),
    Rule(
        "subscription_not_found",
        r"SubscriptionNotFound|InvalidSubscriptionId|subscription .* (could not be found|not found)",
        "The subscription id is wrong, or your account has no access to it.",
        (
            "az account list -o table   (copy the right id into config/azure.local.yaml)",
            "mf init   (re-detects subscriptions and workspaces)",
        ),
    ),
    Rule(
        "workspace_not_found",
        r"ResourceGroupNotFound|ResourceNotFound|WorkspaceNotFound|was not found|could not be found|"
        r"\(404\)|NotFoundError",
        "The resource group or workspace name does not exist (or is in another subscription).",
        (
            "az ml workspace list -o table   (or Azure portal → Machine Learning)",
            "fix `resource_group` / `workspace` in config/azure.local.yaml, or run `mf init`",
        ),
    ),
    Rule(
        "no_permission",
        r"AuthorizationFailed|\(403\)|Forbidden|does not have authorization|AuthorizationPermissionMismatch|"
        r"not authorized|insufficient privileges",
        "You are signed in, but your identity lacks a read role on this workspace (or its storage).",
        (
            "ask the workspace owner for 'AzureML Data Scientist' (or Reader + 'Storage Blob Data Reader')",
            "check the signed-in account: az account show --query user",
        ),
    ),
    Rule(
        "managed_identity_unavailable",
        r"ManagedIdentityCredential.*(unavailable|no response|timed out)|IMDS|169\.254\.169\.254",
        "The managed identity endpoint is not reachable (you are not on an Azure VM / container with an identity).",
        (
            "use `credential: cli` (default) in config/azure.local.yaml on a laptop or workstation",
            "on Azure: assign a user-assigned identity to the VM/container and grant it read roles",
        ),
    ),
    Rule(
        "network_timeout",
        r"timed out|Timeout|ReadTimeout|ConnectTimeout|Connection aborted|ConnectionResetError|"
        r"ServiceRequestError|ServiceResponseError",
        "The network call timed out or was cut off.",
        (
            "retry; if it persists check VPN / proxy (and firewall rules for *.api.azureml.ms, *.blob.core.windows.net)",
            "lower `sync_max_jobs` in config/azure.local.yaml for very large workspaces",
        ),
    ),
    Rule(
        "throttled",
        r"\(429\)|TooManyRequests|throttl|Rate limit",
        "Azure is throttling requests.",
        ("wait a minute and retry; the connector already backs off — lower concurrency if it persists",),
    ),
    Rule(
        "config_invalid",
        r"KeyError: '(subscription_id|resource_group|workspace)'|missing (subscription_id|resource_group|workspace)|"
        r"ValidationError",
        "The workspace configuration is incomplete or malformed.",
        ("compare config/azure.local.yaml with config/azure.example.yaml, or run `mf init`",),
    ),
)

_COMPILED = [(r, re.compile(r.pattern, re.IGNORECASE)) for r in RULES]


@dataclass
class Diagnosis:
    cause: str
    summary: str
    fixes: list[str] = field(default_factory=list)
    matched: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify(detail: str) -> Diagnosis:
    """The first matching rule wins (rules are ordered from specific to generic)."""
    for rule, rx in _COMPILED:
        m = rx.search(detail or "")
        if m:
            return Diagnosis(rule.cause, rule.summary, list(rule.fixes), m.group(0)[:80])
    return Diagnosis(
        "unknown",
        "Unrecognised failure.",
        [
            "re-run with `mf doctor --live --json --bundle support.json` and read the full error",
            "see docs/troubleshooting.md; add a rule to infrastructure/connectors/azureml/diagnose.py",
        ],
    )


@dataclass
class Preflight:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def _env_flags() -> dict[str, str]:
    keys = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "AZURE_CONFIG_DIR")
    out: dict[str, str] = {}
    for k in keys:
        v = os.environ.get(k) or os.environ.get(k.lower())
        if v:
            out[k] = re.sub(r"//[^@/]+@", "//***@", v)  # never print proxy credentials
    return out


def preflight(workspaces: list[dict[str, Any]], az_timeout_s: float = 15.0) -> list[Preflight]:
    """Local checks only (no Azure ML calls). ``az account show`` is called once if the CLI exists."""
    out: list[Preflight] = []
    for mod, pkg in (("azure.ai.ml", "azure-ai-ml"), ("azure.identity", "azure-identity")):
        found = importlib.util.find_spec(mod.split(".")[0]) is not None and _has(mod)
        out.append(
            Preflight(
                f"package {pkg}",
                found,
                "installed" if found else "missing",
                "" if found else "pip install 'model-forensic[azure]'",
            )
        )
    az = shutil.which("az")
    uses_cli = any(str(w.get("credential", "cli")) == "cli" for w in workspaces) or not workspaces
    if az is None:
        out.append(
            Preflight(
                "azure cli",
                not uses_cli,
                "`az` not on PATH",
                "install the Azure CLI, or use credential: managed_identity / default" if uses_cli else "",
            )
        )
    else:
        try:
            res = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [az, "account", "show", "--query", "{tenant:tenantId, user:user.name}", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=az_timeout_s,
                check=False,
            )
            if res.returncode == 0:
                out.append(Preflight("azure cli login", True, "signed in (az account show ok)"))
            else:
                d = classify(res.stderr)
                out.append(Preflight("azure cli login", False, (res.stderr or "").strip()[:200], d.fixes[0]))
        except (subprocess.TimeoutExpired, OSError) as exc:
            out.append(Preflight("azure cli login", False, f"{type(exc).__name__}", "az login"))
    env = _env_flags()
    out.append(
        Preflight(
            "network environment",
            True,
            ", ".join(f"{k}={v}" for k, v in env.items()) or "no proxy / CA variables set",
        )
    )
    for k in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        p = env.get(k)
        if p and not Path(p).is_file():
            out.append(Preflight(f"{k} file", False, f"{p} does not exist", f"point {k} at an existing PEM file"))
    if not workspaces:
        out.append(Preflight("workspaces", False, "none configured", "mf init   (or copy config/azure.example.yaml)"))
    for w in workspaces:
        missing = [k for k in ("subscription_id", "resource_group", "workspace") if not w.get(k)]
        placeholder = [k for k in ("subscription_id", "resource_group", "workspace") if "<" in str(w.get(k, ""))]
        bad = missing + placeholder
        out.append(
            Preflight(
                f"workspace {w.get('name', '?')}",
                not bad,
                "complete" if not bad else f"missing/placeholder: {', '.join(bad)}",
                "" if not bad else "fill config/azure.local.yaml (or `mf init`)",
            )
        )
    return out


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def redact(text: str) -> str:
    """Remove GUIDs (subscription/tenant ids), e-mail addresses and SAS signatures from support output."""
    text = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<guid>", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<email>", text)
    return re.sub(r"(sig|se|sv|sp)=[^&\s\"']+", r"\1=<redacted>", text)
