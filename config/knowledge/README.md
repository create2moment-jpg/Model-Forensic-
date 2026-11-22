# Knowledge

What the team has learnt, independent of any run — kept here as YAML (git-ignored except this README and the
example). Every report (study book, findings, Check my run, Precision & recall) applies the active entries and names
the entry whenever it sets something aside.

* `knowledge.local.yaml` — the editable file (the Knowledge page writes here); other `*.yaml` files are read too.
* Entries imported from the harness (`mf knowledge import`, see `harness/`) arrive as **proposed** and apply only after
  someone accepts them in the Knowledge page.
* Start from `knowledge.example.yaml`.

## Many codebases, one inbox

This folder is the **inbox** (move it with `MF_KNOWLEDGE_INBOX`): the one place coding agents write to — knowledge
YAML as `<topic>-<yyyy-mm-dd>.yaml`, longer explanations as `docs/<codebase>/<topic>.md`. `mf knowledge where` prints
the instruction to put in each codebase's agent instructions (e.g. `.github/copilot-instructions.md`).

Other folders are read too — one per codebase or documentation set:

```bash
export MF_KNOWLEDGE_SOURCE_PERCEPTION=~/work/perception/docs
export MF_KNOWLEDGE_SOURCE_DATA_TOOLS=~/work/data-tools
```

or `knowledge_sources:` in `model-forensic.yaml` (with `codebase`, `trust`, `exclude`, `kind`). Entries from those
folders apply only once accepted (unless `trust: as_written`); their Markdown is searchable on the Knowledge page
(*Documentation*) and in `mf docs search`. `mf knowledge sources` shows what is read from where. See ADR-0009.
