# Site configuration

Everything specific to your environment lives here, as **local files that are never committed**:

| Template (committed) | Your copy (git-ignored) | What |
|---|---|---|
| `azure.example.yaml` | `azure.local.yaml` | Azure subscription, resource group(s), workspace(s), credential mode |
| `team.example.yaml` | `team.local.yaml` | Team policy: de-prioritised classes, expected metric families, metric → code references |
| `storage.example.yaml` | `storage.local.yaml` | Where data goes: the downloads folder (artifacts), metadata cache, index, reports; download workers — also Settings → Storage |
| `../model-forensic.example.yaml` | `../model-forensic.yaml` | App settings: data dir, LLM endpoint, cache size |

Fastest path:

```bash
az login
mf init          # detects subscription + ML workspaces from `az`, asks you to confirm, writes azure.local.yaml
mf doctor --live # checks credential, listing speed, one job's metrics and files
mf serve
```

Or set a single workspace through the environment (CI, containers): `MF_AZURE_SUBSCRIPTION_ID`,
`MF_AZURE_RESOURCE_GROUP`, `MF_AZURE_WORKSPACE` (optional `MF_AZURE_WORKSPACE_TITLE`, `MF_AZURE_TENANT_ID`).
`MF_AZURE_CONFIG` / `MF_TEAM_CONFIG` point to files elsewhere. The Settings page in the UI can detect and save
workspaces too; it writes the same local file.
