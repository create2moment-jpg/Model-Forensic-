# Master prompt v4 — gap report and milestone tracker

The v4 master prompt (30 sections) checked against the repository as it is, before implementing. Status:
✅ done (verified by tests) · 🟡 partial · ❌ missing · ⚠ conflict. Updated as milestones land.

## Section by section (reconnaissance)

| § | Requirement | Status | What exists / what is missing |
|---|---|---|---|
| 1 | Real product, layered, Azure first-class, facts vs derived vs heuristics vs LLM | ✅ | hexagonal layers (ADR-0001), `EpistemicLabel` on findings, deterministic analyses, grounded AI (ADR-0008) |
| 2/25 | Inspect first, TODOs, milestone tracker, never claim unverified | ✅ | this file; `docs/next-steps-prompts.md` progress table |
| 3 | Explicit, observable progress for long operations | 🟡 | jobs report a step; downloads report files/bytes/throughput; no sub-stage list for setup inspection; no elapsed time per step |
| 4 | Main run view fields | 🟡 | id, name, experiment, user, status, start/end, duration, stages strip; no "current stage" field for running pipelines |
| 5 | "Me" identities: profile area, add/remove, *Add to Me* from a run, explicit matching | 🟡 | `me:` list editable in Settings → Azure, `user:me` / *Mine* / *My experiments*; no *Add to Me* on a run; falls back to the `az` signed-in user (explicit, from the login) |
| 5 | "Me" in run-submission workflows | n/a | Model Forensic is read-only towards Azure (AGENTS.md rule 1) — no submission |
| 6 | Two-stage prefetch: manifest/size first, editable selection, then bounded download | 🟡 | per-preset plans (files, bytes, cached) and an artifact catalog exist; download engine has bounded workers, retries, `.part` resume, SHA-256; selection is a preset, not an editable manifest; plan and download are not one task |
| 7 | Stop and resume later, persisted manifest, restart-safe | 🟡 | `.part` resume + content cache mean nothing is fetched twice; jobs persist and become *interrupted*; no Stop (≠ Cancel), no persisted per-file manifest (selected / done / failed / pending), no "reused vs downloaded again" |
| 8 | Dedicated Prefetch Status tab | ❌ | generic Jobs tray only |
| 9 | Background task manager, independent of UI tabs | 🟡 | server-side jobs with persistence, progress, cancellation, rerun; no retry policy per task, no resource accounting |
| 10 | Configurable, validated paths; fast vs bulk storage; what is safe to delete | 🟡 | `data_dir`, `knowledge_dir`, `PROJECT_PATH` / `MF_HOME`; artifact cache, meta cache, index live under `data_dir`; no per-path settings in the UI, no validation (writable / free space), no SSD/HDD split, no deletion guide |
| 11/14/28/29 | Local-LLM project-setup folder + DISCOVER → … → RUN protocol, compatibility report | 🟡 | `harness/` (knowledge from code) and `AGENTS.md` (connection/parsing fixes) exist; no target-setup protocol folder, no machine-readable compatibility report |
| 12 | Multiple projects through the same core, explicit identity and isolation | ❌ | one configuration set (workspaces, naming, team policy, knowledge) per installation |
| 13 | Stable versioned interfaces, adapters, contract tests | 🟡 | ports (`RunSource`, stores, LLM) and two adapters (local, Azure ML); a shared contract suite `tests/contract/` already existed (local + Azure ML with SDK fakes — correction: the first version of this report missed it); no contract version, contract not reusable outside tests |
| 15 | LLM provider configurable; no assumed default endpoint | ⚠ | provider fully configurable; the header plug defaults to `127.0.0.1:8080/v1` (asked for by the user) — must become an explicit, remembered setting; no concurrency limit |
| 16 | Azure inspection: metadata, command, environment, inputs/outputs, logs, metrics… | 🟡 | mapper reads command, environment, inputs, outputs; Compute / Dataset / Logs tabs; command and environment are not shown on the run page |
| 17 | Code diff (`Code → *_repo_diff.txt`) in every relevant stage view | ❌ | not read |
| 18 | Forensic timeline with stages / sub-stages, durations from evidence or marked estimated | 🟡 | pipeline stages with durations; no timeline view, no estimated marker |
| 19 | CPU-aware worker suggestion, override, boost mode, bounded concurrency | 🟡 | `download_workers` (bounded); no suggestion from the machine, no boost mode |
| 20 | Cache integrity, explicit invalidation | ✅ | SHA-256 verified blobs, `.part` never treated as complete, versioned metadata cache, *refresh* on prefetch |
| 21 | Security and privacy | ✅ | site config git-ignored, secrets redacted, path traversal tests, CSP, untrusted-content handling |
| 22 | Engineering standards | ✅ | ruff, `mypy --strict`, typed boundaries, DI in `container.py` |
| 23 | Tests (manifest, stop/resume, paths, identity, contracts, protocol) | 🟡 | 500+ unit tests, 27 e2e journeys; the new areas above have none yet |
| 24/27 | UX acceptance / definition of done | 🟡 | follows from the milestones below |

## Urgent issues and risks found

1. **Prefetch naming collision** — the Runs table's per-row *prefetch* loads *metadata*; v4's *prefetch* downloads
   *artifacts*. Resolved by calling the first **metadata warm-up** (cloud / wheel / tick) and the second
   **artifact prefetch tasks**.
2. **Default LLM endpoint** (§15) — the plug must not assume an address; it asks once, suggests the common local
   address, and remembers the answer.
3. **Project name in code** — the code-diff file is named after one project; the reader uses a configurable pattern
   (generic default) so the core stays project-agnostic (§12, §30).
4. **Single-project configuration** — workspaces, naming and knowledge are global; adding a second project today
   means editing shared files. Project profiles fix this without touching the core.

## Milestones

| # | Milestone | Sections | Status |
|---|---|---|---|
| V4-1 | Prefetch tasks: plan (manifest, sizes, editable selection) → run; Stop / Resume with a persisted manifest; Prefetch tab | 6, 7, 8, 9, 20, 23, 24 | ✅ done — `application/prefetch.py`, `/prefetch*` API, Prefetch page; `test_prefetch_tasks.py` (stop mid-file → restart → resume from the partial offset; cancel removes partials; corrupt / truncated cache files refetched), e2e 21, 28 |
| V4-2 | Project profiles + adapter contract (versioned) + contract tests; local, git-ignored project area | 11, 12, 13, 27 | ✅ done — `projects/` (profile model v1, contract checks as library), naming rules per project, `project=` scoping, `/projects`, `mf projects list|check`, header selector, 3 example profiles, `docs/projects.md`, ADR-0012; `test_projects.py` (3 projects side by side), contract suite extended (library contract + broken adapter), e2e 29 |
| V4-3 | `setup-agent/` prompt folder (01–06 + README) + `mf setup check` compatibility report (JSON + text) | 11, 14, 28, 29 | pending |
| V4-4 | Storage paths in Settings with validation, fast / bulk storage, deletion guide; worker suggestion + boost | 10, 19 | pending |
| V4-5 | Run timeline (stages, sub-stages, evidence vs estimated), current stage, command / environment, code-diff evidence | 4, 16, 17, 18 | pending |
| V4-6 | *Add to Me* from a run, identities in a profile area; LLM endpoint asked not assumed, concurrency limit | 5, 15 | pending |
| V4-7 | Final report: capabilities, tests run, limitations, risks, exact commands | 26 | pending |

## Found and fixed while implementing

* **Cache integrity (§20)** — a cached file was accepted when the size *recorded in the cache database* matched; the
  file on disk was never checked, so a truncated or overwritten file would have been reused. Now the on-disk size is
  always checked, plans and resumes re-hash files up to 256 MB, and stored blobs are read-only (they are hard-linked
  across runs). Test: `test_truncated_cache_files_are_not_reused`, the corrupt-file case in
  `test_cancel_removes_partials_and_corrupt_files_are_refetched`.
* **Test isolation** — after `PROJECT_PATH` / `MF_HOME`, the e2e server (started in a temp folder) wrote its data into
  the checkout's `.mf/`; it now sets `MF_HOME` to its temp folder.
* **Profile loading** — an invalid regex in a profile raised instead of being reported (Python's `re.error` is not a
  `ValueError`); now a readable problem line, the other profiles still load.
