# Task: log patterns — what the training code prints, as patterns Model Forensic can read

You are the expert of this training codebase. Model Forensic reads the job's log files and shows what they say on a
forensic board: facts about the run, problems (errors, warnings), events (checkpoints, evaluations) and curves. It only
understands lines it has a pattern for. The brief's *Log lines seen in this run* lists the lines no pattern explains yet
(each with how often it was printed and its template: numbers → `<n>`, paths → `<path>`).

Find every `print`, `logger.*`, `logging.*`, `tqdm` postfix and framework callback in the code that this run's config
takes that writes a line a developer would want on the board, and write one pattern per kind of line:

1. **id** — `code.<area>.<what>` (e.g. `code.train.step_metrics`, `code.data.loader_stats`).
2. **title** — what the line says, in a few words.
3. **regex** — Python `re` syntax, matching the line as the code formats it; **named groups** `(?P<name>…)` for every
   value worth keeping (use the names the code uses: `loss`, `lr`, `data_time`, `grad_norm`, …). Allow for both
   `nan` / `inf` where a number is printed. Set `flags: i` only when the case really varies.
4. **kind** — `series` (numbers over the step: give `step_field`, the group that is the step / iteration), `fact`
   (a value that describes the run: world size, dataset version, model size) or `event` (something that happened:
   a checkpoint written, an evaluation finished, a resume).
5. **category** — `environment` · `data` · `progress` · `checkpoint` · `evaluation` · `warning` · `error`;
   **severity** — `info` · `attention` · `problem` (a warning the code prints when something is off is `attention`; a
   line printed right before it gives up is `problem`).
6. **example** — one line exactly as it appears in the brief's log lines when it is there; otherwise a line exactly as
   the format string would render it. The regex must match it.
7. **code_ref** — `file:line` of the print / log call.
8. **explain** — what the line means for a developer and, for warnings and errors, what usually fixes it.

Rules: only lines the code really prints (cite them); never invent metric names; do not repeat what a pattern listed
under *Patterns Model Forensic already has* covers; prefer one precise pattern over a broad one (`\bERROR\b` already
exists).

Answer with one YAML file:

```yaml
codebase: <name of the training repository>
patterns:
  - id: code.train.step_metrics
    title: Training step metrics
    regex: 'Iter\s+(?P<step>\d+)/\d+\s+loss=(?P<loss>[-+\d.eEnaif]+)\s+lr=(?P<lr>[-+\d.eE]+)\s+data=(?P<data_time>[\d.]+)s'
    kind: series
    step_field: step
    category: progress
    severity: info
    example: 'Iter 1200/90000 loss=1.8341 lr=2.0e-04 data=0.31s'
    code_ref: tools/train.py:214
    explain: Per-iteration training metrics; data is the time waiting for the batch.
```

Schema: `harness/log_patterns.schema.json`. Patterns arrive in Model Forensic as **proposed**; the developer accepts
them on the run's *Log insights* view before they apply.
