# Local-LLM prompts — problem → prompt → scope → validation

Prompts for a local coding LLM (or any assistant) working on **this machine's configuration**. Each one names its
inputs (commands / endpoints whose JSON you paste), its task, the files it may change (*scope*) and the command that
proves the fix (*validation*). All share the same rules: read-only towards Azure, no site details in git, no core
edits for one project, evidence for every claim.

Start with `mf checks` (or `GET /api/v1/checks`): every failing check names its prompt.

| Problem | Prompt | Checks | Scope (may change) | Validation |
|---|---|---|---|---|
| Something is misconfigured / new machine | [configuration_check_and_repair.md](configuration_check_and_repair.md) | all `CFG-*` | allowed adaptation area | `mf checks` exit 0 |
| Workspace does not connect, runs / stages / metrics missing | [interface_diagnostics.md](interface_diagnostics.md) | CFG-AZ-1, CFG-WS-1 | `config/azure.local.yaml`, `model-forensic.yaml` | `mf doctor --live` all green |
| Runs table metric columns empty or wrong | [metric_columns_diagnostics.md](metric_columns_diagnostics.md) | CFG-MC-1 | `config/projects/*.local.yaml` | `pytest tests/unit/test_metric_columns.py`; `/runs/metric-columns` |
| Run page slow / Light Mode loads the wrong things | [run_light_heavy_audit.md](run_light_heavy_audit.md) | CFG-TASK-1, CFG-TASK-2 | none (report; changes via Test Space proposals) | `mf checks` CFG-TASK-* pass |
| Task costs not measured for this site | [test_space_profile.md](test_space_profile.md) | CFG-TASK-2, CFG-TS-1 | `config/test-space/test-space.yaml` | experiment `done`, n ≥ min_samples |
| A belief about performance to confirm or refute | [test_space_hypothesis.md](test_space_hypothesis.md) | CFG-TS-1 | none | experiment `verdict` |
| Prefetch slow, stalled or failed | [prefetch_observability_diagnostics.md](prefetch_observability_diagnostics.md) | CFG-PF-1 | `download_workers` in `model-forensic.yaml` | task `completed` / not stalled; CFG-PF-1 pass |
| Which plots answer my question | [plot_selection.md](plot_selection.md) | — | none (specs are data) | `POST /plots/validate` → ok |
| A head of a multi-task model is neglected | [uncertainty_loss_forensics.md](uncertainty_loss_forensics.md) | — | none (proposals for the training code owners) | `/runs/<new>/uncertainty` status improves |

Related: `setup-agent/01–06` (connecting a new target project), `harness/` (answering questions from inside the
training code), `config/test-space/README.md` (how proposals are reviewed).
