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

On an SSH machine, `mf serve` prints the tunnel command to run on your laptop — or use `mf serve --remote` and open
the link it prints (protected by an access token).

A short tour starts on first visit (replay it with `?tour=1`). **Today** answers your newest run in one sentence; the
sidebar has five places — Today, Runs, Compare, Studies, Knowledge — and each shows its sections above the page
(Today → *Check a run* for any run in plain language). Press `?` for every shortcut. In the terminal:

```bash
mf check                         # the newest completed run, in plain language
mf check lr-sweep-1e3            # a run that diverged
mf verdict --run local:baseline-cl4f-s0   # one sentence, headline numbers vs a baseline picked for you, next step
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

* **Runs** → search (`user:me since:7d`, `status:failed`, or plain English), or paste a run id or an Azure ML studio
  link into ⌘K → open a run. `j` / `k` move through the table, Enter opens.
* The run page opens with the **verdict**: one sentence, up to three headline numbers with their trend against a
  baseline picked for you (the reason is shown — change it in one click), and one next step. Then five places:
  **Summary · Metrics · Review · Files · Timeline**; **Ask** opens beside the view; everything else is in `⋯`.
* **Review** → *Findings* → *Analyze* runs the deterministic analyses; every finding shows its evidence, *Why this
  confidence?*, and *Is this finding right?*.
* **Compare** two runs: one sentence first (better / worse on how many metrics, what changed in the config), then
  noise-aware deltas and the config diff; `⋯` → *Copy summary for a PR*.
* A run still training: `⋯` → *Watch this run* — you are told when it finishes, fails or develops a problem.

Next: [run a study](studies.md) when you want to answer a question ("is my change better?") rather than look at
one run.

## Opening runs fast

On the Runs page, the cloud icon on a row **prefetches** that run: pipeline stages, configs, metrics and the file
list are loaded in the background (the icon turns while it works) and kept on disk for finished runs, so the run
page opens immediately — also after a restart. Select several runs and press *Prefetch* to warm them all.
*My experiments* (or `experiment:mine` in the search box) shows every run in the experiments you have runs in;
star an experiment (☆ next to its name) and *★ Starred experiments* (`exp:starred`) shows theirs.

## Workspaces and local folders

The workspace dropdown in the top bar scopes everything to one workspace; it shows each workspace's runs and last
sync with a sync button, and a workspace with nothing indexed syncs when you pick it — no restart, no `mf sync`. Add
workspaces from the `az login` list in **Settings**. Training folders on this machine are read in place: **Runs →
Local folders** (browse or type the path with autocomplete).

## Make it yours

**Settings → Appearance**: Midnight (default), Graphite, Ocean, Ember, Obsidian or Daylight; expressive touches
and motion on or off. **Settings → Navigation**: *Classic* brings back the old sidebar. **Settings → Editor**: code
references open in VS Code, Cursor or JetBrains.
