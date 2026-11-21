# Troubleshooting

Start here, every time:

```bash
mf doctor --live                         # preflight + every workspace: token, listing, run, metrics, files
mf doctor --live --json --bundle support.json   # the same as JSON; the bundle has ids and e-mails redacted
```

Each failed check prints a **cause** and the **fixes** in order. The causes come from
`backend/src/model_forensic/infrastructure/connectors/azureml/diagnose.py` (one rule per cause, with the text that
matched), so you — or a local coding LLM — can see why a cause was chosen. If the cause is `unknown`, add a rule
there with an example message in `backend/tests/unit/test_diagnose.py`.

## Connection

| Cause (`mf doctor`) | What you see | Fix |
|---|---|---|
| `not_installed` | "Azure ML support not installed" | `pip install 'model-forensic[azure]'` in the same venv as `mf` |
| `not_logged_in` | "Please run 'az login'", "ChainedTokenCredential failed" | `az login`; `az account set -s <id>` |
| `token_expired` | `AADSTS700082`, "refresh token has expired" | `az login` again |
| `wrong_tenant` | `InvalidAuthenticationTokenTenant`, `AADSTS50020` | `tenant_id:` for the workspace in `config/azure.local.yaml`; `az login --tenant <id>` |
| `subscription_not_found` | `SubscriptionNotFound` | `az account list -o table`; fix the id or re-run `mf init` |
| `workspace_not_found` | `ResourceGroupNotFound`, `ResourceNotFound`, 404 | `az ml workspace list -o table`; fix `resource_group` / `workspace` |
| `no_permission` | `AuthorizationFailed`, 403 | ask for *AzureML Data Scientist* (or Reader + *Storage Blob Data Reader*) |
| `managed_identity_unavailable` | `ManagedIdentityCredential … unavailable`, IMDS | on a laptop use `credential: cli` (the default) |
| `tls_interception` | `CERTIFICATE_VERIFY_FAILED`, self-signed certificate | `export REQUESTS_CA_BUNDLE=<corporate-ca.pem>` and `SSL_CERT_FILE` to the same file; Docker: `deploy/ca/` |
| `proxy` | `ProxyError`, 407 | set `HTTPS_PROXY` / `NO_PROXY` (credentials in the URL if required) |
| `dns` | "Name or service not known", "Failed to resolve" | VPN on (private endpoints); `nslookup management.azure.com` |
| `network_timeout` | `ReadTimeout`, "Connection aborted" | retry; check VPN/proxy; lower `sync_max_jobs` |
| `throttled` | 429 | wait; the connector backs off automatically |
| `config_invalid` | `KeyError: 'subscription_id'` | compare with `config/azure.example.yaml` or run `mf init` |

### Slow, not broken

`mf doctor --live` marks a check `!` when it exceeds its budget (token 5 s, first job page 10 s, metrics 15 s,
files 30 s).

* **Token takes minutes:** the default credential chain probes managed identity first on some machines. Keep
  `credential: cli` (Azure CLI first) — that is the default.
* **Listing is slow:** keep `fast_listing: true` (raw ARM listing, about 1 s per page).
* **Many jobs:** `sync_max_jobs` limits how many of the newest jobs are indexed per workspace.

### Data looks wrong or empty

```bash
mf inspect <downloaded run folder or the v1 outputs/_azure_artefacts>   # how every file is read
mf inspect <folder> --json
```

* **Metrics under "unknown" split** → the names did not map: `mf doctor --live` lists unmapped examples under
  `naming`. Mappings live in `backend/src/model_forensic/infrastructure/connectors/naming.py`.
* **"recognised but no values extracted"** → the evaluation JSON format drifted; readers are in
  `backend/src/model_forensic/analysis/evaljson.py` (and `analysis/sequences.py` for parquets).
* **No pipeline stages** → `metrics_api: runhistory` (default) is needed for stages and stage outputs.

## Web UI and server

| Symptom | Fix |
|---|---|
| Blank page at `mf serve` | build the UI once: `cd web && npm ci && npm run build` (or set `MF_WEB_DIST`) |
| Port in use | `mf serve --port 8790` |
| Shared mode refuses to start | `server.auth` is `none` on a public interface — use `header` or `oidc` (docs/deploy.md) |
| 401 in shared mode | the proxy did not pass `X-Forwarded-Email`, or the user's domain is not in `allowed_domains` |
| AI says "offline heuristic" | no LLM configured: Settings → LLM, or `llm:` in `model-forensic.yaml` |
| Container exits with "Permission denied: '.mf'" | images before 1.0 — rebuild; the data dir is `/data` (mount a volume) |

## Asking a local coding LLM to fix it

Give it `AGENTS.md`, this page, and the redacted `support.json` from `mf doctor --live --json --bundle`. The
bundle has the version, platform, the preflight and every check with its diagnosis — enough to locate the code.
