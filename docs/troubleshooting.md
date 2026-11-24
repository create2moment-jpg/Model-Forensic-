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
  `naming`. Mappings live in `backend/src/model_forensic/domain/naming.py`.
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
| A pipeline shows 0 metrics / only its own 3 log files | fixed after 1.0.0rc2: stages are resolved once per run and kept; if it persists, the run page shows the reason (`stages_error`) |
| `user:me` / *Mine* says "Who is 'me'?" | add `me: ["Your Name", "you@company.com"]` under `defaults:` in `config/azure.local.yaml` (Settings → Azure → *Me*), or re-run `mf init` |

## Local LLM (llama.cpp, LM Studio, Ollama, vLLM)

Base URL e.g. `http://127.0.0.1:8080/v1` (the `http://` is added if you leave it out). Settings → LLM → *Test
connection* now checks **chat**, **JSON mode** and **tool calling** separately:

| Result | Meaning / fix |
|---|---|
| chat ✗ "transport error" | server not reachable from where `mf` runs — inside a container `127.0.0.1` is the container itself; use the host's address (`host.docker.internal`) or run the server in the same container |
| chat ✓, JSON mode noted as unsupported | fine — answers are requested as plain text and parsed |
| tools noted as unsupported | fine — the AI answers from the evidence it is given; for llama.cpp start the server with `--jinja` to enable tools |
| "larger than the model's context window" | lower *Context limit* (e.g. 8000) or start the server with a larger context (`-c 16384`) |

Requests to `localhost` / `127.x` never use `HTTP(S)_PROXY`, and `<think>…</think>` blocks of reasoning models are
removed before parsing.
| Container exits with "Permission denied: '.mf'" | images before 1.0 — rebuild; the data dir is `/data` (mount a volume) |

## Asking a local coding LLM to fix it

Give it `AGENTS.md`, this page, and the redacted `support.json` from `mf doctor --live --json --bundle`. The
bundle has the version, platform, the preflight and every check with its diagnosis — enough to locate the code.

## "not a folder" / config not found when running `mf` from another folder

Relative paths resolve from two roots — check them with `mf paths`:
your data (`mf inspect outputs/_azure_artefacts`, local run folders, knowledge sources) from `PROJECT_PATH`;
Model Forensic's files (`config/`, `.mf/`, `harness/`, `model-forensic.yaml`) from the Model Forensic folder
(`MF_HOME`). Set `PROJECT_PATH` once with `scripts/set-project-path.sh` (writes it to `~/.bashrc` and `~/.zshrc`).

## `mf serve` runs but the page does not load

| What you see | Cause | Fix |
|---|---|---|
| "The server runs — the web UI is not built" (or `{"detail":"Not Found"}` on an older version) | no `web/dist/index.html` where the server looks | `cd web && npm ci && npx vite build`, then `mf stop; mf serve`; or `MF_WEB_DIST=/path/to/web/dist mf serve`. `mf checks` → `CFG-UI-1` |
| Browser: "can't connect" / spins, the server is on another machine (SSH, VM, dev box) | `127.0.0.1` in your browser is your laptop | either the tunnel — from the laptop `ssh -N -L 8765:127.0.0.1:8765 you@machine` (`mf serve` prints it with your user and the machine's address filled in; VS Code / Cursor Remote-SSH forward the port by themselves), then open `http://127.0.0.1:8765` — or `mf serve --remote` on the machine and open the link it prints (`…/?token=…`; `mf status` prints it again) |
| "Access token needed" / API answers 401 | the server runs with `--remote`: nothing without its access token | open the link `mf serve --remote` printed (or `mf status`) once — the browser stays signed in for 30 days; scripts send `Authorization: Bearer <token>` or `X-MF-Token`. A fixed token: `mf serve --remote --token …` or `MF_ACCESS_TOKEN` |
| "already running at …" | a server is already on that port | open that URL, or `mf stop --port 8765` |
| "port 8765 is in use by another program" | something else uses the port | `mf serve --port 8766` |
| Old screens after an update | the UI build is from before the update | rebuild (`npx vite build`) and reload with Ctrl+Shift+R |

`mf status` shows what is running (URL, pid, memory); the API answers at `/api/v1/health` even without a UI build.
