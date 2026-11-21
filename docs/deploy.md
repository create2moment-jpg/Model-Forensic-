# Shared deployment

Local single-user mode stays the default (`mf serve` on 127.0.0.1, no login). A **shared instance** gives the team
one URL with company sign-in, background sync, alerts and shared studies.

## Architecture

```
browser ──TLS──▶ ingress / App Gateway ──▶ oauth2-proxy (Entra ID) ──▶ model-forensic (FastAPI + web UI)
                                                                         │  read-only
                                                                         ▼
                                                     Azure ML workspaces (managed identity)
```

* **Who you are:** oauth2-proxy signs users in with Entra ID and passes `X-Forwarded-Email`; Model Forensic trusts
  that header only in `server.auth: header` mode — expose the app **only** through the proxy. Alternatively
  `server.auth: oidc` validates Entra ID bearer tokens in the app itself (issuer, audience, signature, expiry).
* **What Azure sees:** one read-only identity (managed identity / workload identity) with *Reader* +
  *AzureML Data Scientist (read)* on the workspaces. Users never need Azure permissions to browse; the tool never
  writes to Azure. Use `credential: default` (or `managed_identity`) in `config/azure.local.yaml` inside the
  container — `az login` is for local mode.
* **What is shared:** the run index, findings, studies (owner, note authors, decision maker and every timeline
  event record the user), study books. Settings changes (Azure workspaces, LLM, plugin reload) need an admin.

## Steps

1. **App registration** in Entra ID: redirect URI `https://<host>/oauth2/callback`; create a client secret.
2. **Identity for Azure ML:** a user-assigned managed identity with read roles on each workspace.
3. **Config** — on the host, a `config/` folder (mounted read-only at `/config`):
   - `model-forensic.yaml` from `deploy/model-forensic.shared.example.yaml` (server mode, allowed domains, admins,
     sync interval, alerts, nightly books);
   - `azure.local.yaml` (workspaces, `credential: managed_identity`), `team.local.yaml` (optional).
4. **Secrets** — `.env` next to `docker-compose.yml` from `deploy/.env.example` (tenant, client id/secret, cookie
   secret, Teams webhook URL). Never commit it (`.env` is git-ignored).
5. `docker compose up -d --build`, put TLS in front of port 4180, open `https://<host>`.
6. Check: `docker compose exec model-forensic mf doctor --live`; `GET /api/v1/scheduler` shows the last sync.

## Server settings (`server:` in model-forensic.yaml)

| Key | Meaning |
|---|---|
| `mode` | `local` (default) or `shared` |
| `auth` | `header` (trusted proxy header), `oidc` (validate tokens), `none` (refused on a public interface) |
| `trusted_header`, `name_header` | header names for `header` mode (App Service Easy Auth: `X-MS-CLIENT-PRINCIPAL-NAME`) |
| `oidc_issuer`, `oidc_audience` | for `oidc` mode, e.g. `https://login.microsoftonline.com/<tenant>/v2.0`, `api://<client-id>` |
| `allowed_domains`, `allowed_users` | who may sign in (empty = anyone the proxy lets through) |
| `admins` | may change settings |
| `sync_minutes` | background incremental sync interval (0 = off) |
| `notify_webhook_env`, `notify_on` | env var holding a Teams incoming-webhook URL; `failed` = alert on newly failed runs |
| `nightly_books_utc`, `book_studies` | regenerate these studies' books daily at this UTC time (and notify) |

## Operations

* **Backup:** `mf backup --out mf-$(date +%F).tar.gz` — consistent SQLite snapshots + study books. Restore: stop the
  container, extract into the data volume, start.
* **Upgrade:** pull, `docker compose up -d --build`; stored documents carry a schema version and are migrated on read.
* **Health:** container `HEALTHCHECK` on `/api/v1/health`; Diagnostics page; `/api/v1/scheduler`.
* **Logs:** structured JSON on stdout; secrets and keys are redacted.
