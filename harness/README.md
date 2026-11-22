# Harness adaptation — teach Model Forensic about any training codebase

Model Forensic reads runs; it cannot read your training code. A coding agent inside your project (e.g. Copilot with
the codebase and its configs open) can. The harness is the contract between the two:

```
Model Forensic ──(brief: what the runs show)──▶ coding agent in your project ──(knowledge YAML)──▶ Model Forensic
```

1. **Brief.** `mf harness brief --run <run id> --task loss_composition > brief.md`
   (or *Harness brief* on a run page). It lists what the tool observed — stages, config keys and values, loss and
   metric names, datasets, the knowledge it already has — and appends the task.
2. **Ask.** Open your coding agent inside the training project, give it `brief.md`. The task tells it what to find in
   the code and to answer **only** with YAML in the knowledge schema (`knowledge.schema.json`), citing `file:line`.
   The brief's *Where to write* section names the **inbox** — the agent saves its YAML (and any longer Markdown
   explanation) there, so knowledge always accumulates in one place (`mf knowledge where`, ADR-0009).
3. **Import.** Files the agent saved in the inbox are read automatically (as proposed); or `mf knowledge import answer.yaml` (or Knowledge page → *Import*). Entries are validated and stored as
   **proposed**; nothing changes in any report until you **accept** them in the Knowledge page.
4. **Use.** Accepted entries apply everywhere: the study book, findings, Check my run, Precision & recall — and
   every report names the entry whenever it sets something aside.

Repeat per setup (e.g. one camera + radars, more cameras, camera + lidar; ground-truth vs online labels): the setup
entries let later entries say `when: {setup: [...]}`.

## Tasks

| Task | Finds in the code | Typical entries |
|---|---|---|
| `setup_profiles` | config keys/values that identify each sensor setup and label source | `kind: setup` with `when.config` |
| `loss_composition` | every loss term, its weight, which head/sensor it belongs to, which config enables it | `fact` with code refs; `caveat` for disabled terms |
| `uncertainty_weighting` | which losses are uncertainty-weighted, σ initialisation / clamps, the regularisation term | `fact` / `caveat` (e.g. "the weighted total includes +log σ") |
| `label_targets` | which label sources provide which targets (velocity, acceleration, attributes) | `constraint` with `ignore_metrics` (e.g. no velocity for manual labels) |
| `metric_definitions` | how each evaluation metric is computed, the combined-metric ruler, matching thresholds | `fact` with code refs; `caveat` for misleading names |
| `checkpoint_and_validation` | checkpoint monitor, validation loop, early stopping | `constraint` / `caveat` |

## Rules the agent must follow (they are in every task too)

* Cite `file:line` for every claim (`source.code_refs`); no claim without a reference.
* Never invent config keys or metric names — use the ones in the brief, or the exact ones found in the code.
* Prefer fewer, precise entries; set `confidence` honestly; say in `why` what would change it.
* Output YAML only: `entries: [...]` per `knowledge.schema.json`. Ids: `K-…` (constraint/fact/caveat), `S-…` (setup).

`mf harness schema` regenerates `knowledge.schema.json` from the model (a test fails if it is out of date).
