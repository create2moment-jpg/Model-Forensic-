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
| V4-3 | `setup-agent/` prompt folder (01–06 + README) + `mf setup check` compatibility report (JSON + text) | 11, 14, 28, 29 | ✅ done — `setup-agent/`, `application/setup_check.py` (satisfied / missing / incompatible / unknown / optional, fixes, allowed / forbidden paths, privacy via `git check-ignore`, `--live` contract), recorded in git-ignored `config/setup/`; `test_setup_check.py` (synthetic targets, privacy leak detection, protocol folder) |
| V4-4 | Storage paths in Settings with validation, fast / bulk storage, deletion guide; worker suggestion + boost | 10, 19 | ✅ done — `StorageConfig` + `Settings.paths()` (one place paths are derived), `application/storage.py` (validation with a real write probe, SSD/HDD from the OS, usage, file guide, cgroup-aware worker suggestion + boost), `config/storage.local.yaml`, `/settings/storage*`, Settings card, `docs/storage.md`; `test_storage.py` + e2e 30 |
| V4-5 | Run timeline (stages, sub-stages, evidence vs estimated), current stage, command / environment, code-diff evidence | 4, 16, 17, 18 | ✅ done — `application/timeline.py` (stage roles, sub-stages measured vs estimated, clamped or *not placed* when outside the measured window, current stage, evidence per stage), code diff found by profile / default globs and shown raw with counts in every stage that has one; run *Timeline* tab, current-stage pill; `test_timeline.py` + e2e 31 |
| V4-6 | *Add to Me* from a run, identities in a profile area; LLM endpoint asked not assumed, concurrency limit | 5, 15 | ✅ done — `/me/names` (exact names, persisted to `defaults.me` only), Settings *Who is “me”*, *Add to Me* on a run; `local_llm_url` empty by default (asked once, remembered in `llm.json`), `max_concurrency` (bounded semaphore); `test_me_names.py`, `test_llm_connect.py` (concurrency peak = 2 of 6) + e2e 27, 32 |
| V4-7 | Final report: capabilities, tests run, limitations, risks, exact commands | 26 | ✅ below |

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
* **Stage roles** — `evaluate_pretrain` was classified as training ("pretrain" contains "train"); evaluation patterns
  now win.
* **Estimated durations** — spans from metric timestamps could exceed the measured stage window (a 13.9 h training
  inside a 9 h stage); they are clamped, or marked *not placed* when entirely outside, with the reason.
* **Metadata disk cache and local folders** — a local run folder changed after it finished (a file added) was hidden
  by the disk cache after a restart; only remote sources (Azure ML) use the disk cache now. Test:
  `test_local_folders_are_never_disk_cached`.
* **Route clash** — a new `GET /me` for identity names would have been shadowed by the existing `GET /me` (signed-in
  identity); the names live at `/me/names`, and a test calls both.

## Final report

### What exists now (verified)

| Area | Capability | Where |
|---|---|---|
| Prefetch | plan → editable selection → run; stop / resume (also after a restart), cancel; verified reuse; Prefetch page | `application/prefetch.py`, `/prefetch*`, `web/src/pages/Prefetch.tsx` |
| Background work | server-side tasks independent of the UI; persisted; progress, speed, time left; bounded workers | `JobRegistry`, `PrefetchManager` |
| Integrity | on-disk size always checked, SHA-256 on plan / resume (≤ 256 MB), `.part` never trusted, read-only blobs | `infrastructure/artifacts/store.py` |
| Projects | versioned profiles (contract v1), many projects side by side, project selector, naming rules per project | `projects/`, `config/projects/`, `docs/projects.md`, ADR-0012 |
| Adapter contract | 8 checks as library code; CI on every adapter + a broken one; `mf projects check` on real workspaces | `projects/contract.py`, `tests/contract/` |
| Setup protocol | `setup-agent/` 01–06 + README; `mf setup check [--live] [--json]` | `setup-agent/`, `application/setup_check.py` |
| Paths | `PROJECT_PATH` / `MF_HOME`, per-role storage paths validated in Settings, deletion guide | `shared/paths.py`, `application/storage.py`, `docs/storage.md` |
| Resources | CPU- (cgroup) and disk-aware worker recommendation, boost with implications, custom value | `application/storage.py::suggest_workers` |
| Run view | id, name, experiment, owner, status, current stage, duration, start / end, command, environment; timeline with measured vs estimated spans; evidence per stage | `application/timeline.py`, Timeline tab |
| Code diff | found by profile / default globs, shown raw with counts in every stage that has one | `/runs/{run}/code-diff` |
| Identity | explicit "me" names, editable, *Add to Me*, Mine / My experiments | `/me/names`, Settings |
| LLM | provider interface, no assumed endpoint, concurrency bound, optional everywhere | `infrastructure/llm/`, `application/llm_connect.py` |

### Tests run (this report)

* `ruff check src tests` — all checks passed; `mypy --strict src` — no issues in 132 files.
* `pytest` — **519 passed** (unit, integration, contract).
* Playwright — **32 user journeys passed** (prefetch plan / start / complete, warm-up, projects selector, storage
  validation, timeline + code diff, *Add to Me*, local LLM plug, knowledge, drafts, studies, compare, …).
* Not run here: anything against a real Azure ML workspace or a real local LLM (no access from this environment) —
  `mf doctor --live`, `mf projects check`, `mf setup check --live` are the commands for that.

### Limitations

* Stop / resume is verified with the local adapter and the shared contract; Azure blob downloads use the same
  `open_artifact(offset)` path but were not exercised live here.
* Sub-stage durations inside a stage are estimates from metric timestamps; SSD / HDD detection is what the OS reports
  (Linux; "unknown" elsewhere; virtual disks often report "rotating").
* Paths changed in Settings apply at the next start and nothing is moved automatically.
* A project profile cannot express arbitrary logic — by design; gaps are reported as interface gaps.

### Remaining risks

* The first live run against your workspace may show adapter gaps the fakes do not model (rate limits on large
  listings, unusual artifact layouts) — `mf setup check --live` and `mf projects check` will name them.
* `forensic.sqlite` holds your work: back it up (`mf backup`) before moving `index_dir`.

### Exact commands

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e "backend[dev,azure]"
(cd web && npm ci && npx vite build)
scripts/set-project-path.sh /path/to/project && source ~/.bashrc
mf setup check                                   # what is satisfied / missing, where changes are allowed
mf init                                          # Azure ML workspaces → config/azure.local.yaml
cp config/projects/<kind>.example.yaml config/projects/<id>.local.yaml   # adapt (setup-agent/04)
mf projects check <id> && mf setup check --live  # validate
mf serve                                         # http://127.0.0.1:8765
# development
cd backend && ruff format src tests && ruff check src tests && mypy src && pytest -q
cd web && npx tsc -b && npx vite build && npx playwright test --trace off
```
