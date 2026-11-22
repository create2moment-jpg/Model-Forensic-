# Storage — where Model Forensic keeps its data, and what may be deleted

Everything lives under `data_dir` (default `.mf/` in the Model Forensic folder) unless a role is moved. Change it in
**Settings → Storage & performance** (validated: exists or creatable, writable — a real test write — free space,
disk type) or in `config/storage.local.yaml` (git-ignored):

```yaml
storage:
  artifacts_dir: /mnt/bulk/model-forensic/artifacts   # bulk: downloaded files (large; an HDD is fine)
  meta_dir: /mnt/ssd/mf/meta                           # fast: metadata cache of finished runs
  index_dir: /mnt/ssd/mf                               # fast: run index, documentation index, your work database
  work_dir: /mnt/ssd/mf/books                          # fast: generated study books and reports
download_workers: 8
```

Relative paths resolve under the Model Forensic folder (`MF_HOME`). Paths apply at the next start (nothing is
copied — move existing files yourself, or let caches refill); the worker count applies to the next download.

## What is safe to delete

| File / folder | Kind | Deleting it |
|---|---|---|
| `forensic.sqlite` | **required** | loses studies, findings, drafts, belief verdicts, jobs, prefetch tasks — back it up (`mf backup`) |
| `index.sqlite` | rebuildable | `mf sync` rebuilds it; favourite marks are lost |
| `docs.sqlite` | rebuildable | the documentation index is rebuilt on the next search |
| `artifacts/` | cache | files are downloaded again when needed (content-addressed, SHA-256 verified) |
| `artifacts/**/*.part` | resumable | partial downloads; a stopped prefetch resumes from them — deleting restarts those files |
| `meta/` | cache | finished runs' metadata is read from the backend again |
| `books/` | cache | regenerate a study book from its study |
| `llm.json` | settings | the LLM connection saved in the UI (no key); deleting falls back to `model-forensic.yaml` |

Integrity: a cached file is reused only when its size on disk matches what was recorded (and, when planning or
resuming a prefetch, its SHA-256 for files up to 256 MB); partial `.part` files are never treated as complete.

## Parallel downloads

Downloads wait on the network, so the recommendation is about two workers per usable CPU (cgroup quota aware,
capped at 16; at most 4 on a rotating disk). **Boost** doubles it (max 32): faster on a fast link, at the cost of more
memory, more load on the storage account (Azure may throttle with HTTP 429 — retries then slow it down) and more
parallel disk writes. Workers are always bounded; every download task uses the store's fixed pool.
