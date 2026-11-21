# Getting started in 10 minutes

You need Python ≥ 3.10, Node ≥ 20 (only to build the UI once) and, for your own runs, the Azure CLI.

## 1. Install and try the demo (3 min)

```bash
git clone <this repo> model-forensic && cd model-forensic
python -m venv .venv && source .venv/bin/activate
pip install -e "backend[azure]"
(cd web && npm ci && npm run build)
mf demo            # 24 synthetic runs, indexed locally — no cloud needed
mf serve           # http://127.0.0.1:8765
```

A short tour starts on first visit (replay it with `?tour=1`). Try **Check my run** in the sidebar, or in the
terminal:

```bash
mf check                         # the newest completed run, in plain language
mf check lr-sweep-1e3            # a run that diverged
```

## 2. Connect your Azure ML workspace (3 min)

```bash
az login
mf init                          # detects subscription / resource group / workspace from your login
mf doctor --live                 # token → job listing → a run → metrics → files, each with a time budget
```

`mf init` writes `config/azure.local.yaml` (git-ignored — nothing about your site goes into git). If a check fails,
`mf doctor --live` prints the cause and the fixes; see [troubleshooting](../troubleshooting.md).

## 3. Look at your own run (4 min)

```bash
mf serve
```

* **Runs** → search (`user:me since:7d`, `status:failed`, or plain English) → open a run.
* The run page opens with a **story strip**: verdict, the top problems, what to do next.
* **Findings** → *Analyze* runs the deterministic analyses; every finding shows its evidence, *Why this
  confidence?*, and *Is this finding right?*.
* **Compare** two runs: noise-aware metric deltas and a semantic config diff.

Next: [run a study](studies.md) when you want to answer a question ("is my change better?") rather than look at
one run.
