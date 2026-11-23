# Model Forensic — product redesign ("it just answers")

Status: **waves 1 and 2 built** (run page calmed, verdict on the run page, Today and Compare) · waves 3–5 proposed · audit of the UI as of 2026-11-23.

## The one-line idea

A developer opens Model Forensic with one of three questions. The product should answer the question on the first
screen, in one sentence, with the evidence one click away:

1. **Is my run OK?** (health: diverging, plateaued, NaN, a stage failed)
2. **What changed, and did it help?** (against the right baseline, picked for me)
3. **What should I try next?** (one recommended experiment, with the reason)

Everything else (tabs, analyses, downloads, dossiers, prompts) is how we get the answer. Today we show the *how*
first and the answer second. The iPhone move is to reverse that: **answer first, machinery hidden, power one gesture
away**.

## What the audit found

| Where | Today | Why it hurts |
|---|---|---|
| Sidebar | **18 destinations**: Check my run, Command center, Studies, Runs, Local runs, Progression, Compare, AI investigation, Knowledge, Prefetch, Test Space, Library, Reports, Ledger, Jobs, Diagnostics, Settings, theme | No obvious place to start. Several are the same job: Local runs is Runs, Progression is Compare, Jobs and Prefetch are background work. |
| Asking "what's wrong?" | **6 entry points**: Check my run, the home "Insight" card, Expert review, Findings + Analyze, AI investigation, Check-this-run banner | Each gives a slightly different answer in a different layout. The user can't tell which one is the real one. |
| Run page header | **7 buttons**: Analyze, Compare, Bundle, Report, Ask AI, Dossier for the LLM, Study | Seven equal-weight choices before you have seen anything about the run. |
| Run page tabs | **16 tabs**: Overview, Expert review, Timeline, Metrics, Plots, Uncertainty, Setup, Findings, Precision & recall, Sequences, Config, Dataset, Compute, Logs, Artifacts, AI | They scroll off screen. Plots and Metrics overlap; Dataset, Compute and Setup are three views of one thing. |
| Loading | A panel saying "Loaded 1/1 part in 6 ms" with **Light / Full** and "Load metrics in background" | Our caching strategy is shown to the user as a decision they have to make. |
| Complexity | **Simple / Standard / Expert** switch, plus Settings → Features to hide tabs | We ask users to design the product for themselves. |
| Copy | Most cards carry a sentence of implementation detail ("a name logged by several stages gets stage=… and the last stage is the headline", "reads its metric histories … heavy on Azure ML, so it runs when you ask") | Reads like a design doc. Users skim it and stop trusting the subtitles. |
| Home | 4 counter tiles (Indexed runs 24, Created this week 20, …) and a "Check it" button for the newest run | The counters don't lead to an action; the insight is one click away instead of just being there. |
| Overview | "What changed?" opens as an empty card ("No baseline selected") | An empty state where we could have picked a baseline and shown the answer. |
| Jargon | Prefetch, Test Space, Library, Dossier, Light Mode, Indexed, offline-heuristic (no LLM), role=…/family=… chips | Internal words in the UI. |

## Principles (the checklist every screen is held to)

1. **Answer first.** The top of every page is a sentence and three numbers, not a toolbar.
2. **One primary action per screen.** Others live in a `⋯` menu or the ⌘K palette.
3. **The product decides the defaults.** Baseline, stage, smoothing, what to load: chosen for you, changeable in one
   click, never asked up front.
4. **Never show the machinery.** No cache modes, task kinds or byte budgets in the main UI. Loading is a thin progress
   line and skeletons that fill in place.
5. **Progressive disclosure, not modes.** No Simple/Expert switch: the simple answer is on top, and the expert detail is
   one "Show the evidence" away, on the same page.
6. **Every view is a link and a command.** Each screen has a stable URL and a "Copy as `mf …`" so it can be shared,
   scripted or pasted into a PR.
7. **Words a developer uses.** "Baseline", "diverged at step 12k", "loss/bbox is 8% worse", never "observation source:
   drift".
8. **Fast is a feature.** Anything under 100 ms appears without a spinner; anything longer shows what it is doing and
   how long it usually takes.

## The new shape

### Navigation: 5 places instead of 18

| New | Absorbs |
|---|---|
| **Today** | Command center, Check my run (newest run's verdict shown right away), Needs attention, Running now, Continue where you left off |
| **Runs** | Runs, Local runs (a "Source" filter + "Add a local folder"), Prefetch (a "Keep offline" action on selected runs) |
| **Compare** | Compare, Progression (pick 2 runs → compare; pick 3+ → progression; same page) |
| **Studies** | Studies, Ledger (a study's decision log), Test Space, Reports (a study's output) |
| **Knowledge** | Knowledge, Library |

Gone from the sidebar:
- **AI investigation** becomes "Ask" in ⌘K and on every page.
- **Jobs** moves to the activity indicator in the top bar.
- **Diagnostics** becomes Settings → Connection.
- **Theme** follows the system, with an override in Settings.

### Run page: a verdict, then the evidence

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ sparse_detection temporal finetune          ● completed · 12 h · dana         │
│                                                    [ Compare ]  [ Ask ]  [⋯] │
├──────────────────────────────────────────────────────────────────────────────┤
│  ✓ Healthy run — NDS 0.618, +2.1% vs baseline xf-sparse-uw-bounded.            │
│    One thing to look at: loss/bbox[layer=0] stopped improving at step 14k.     │
│                                                                                │
│   NDS 0.618 ▲2.1%   ▁▂▄▆▇   mAP 0.512 ▲0.8%   ▁▃▅▆▇   train/loss 0.41  ▁▃▂▂▁  │
│                                                                                │
│   Next: try the cosine schedule from xf-sparse-uw-bounded (why ›)   [Start ›] │
├──────────────────────────────────────────────────────────────────────────────┤
│  Summary   Metrics   Review   Files   Timeline         stage: all ▾           │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Verdict card.** One sentence built from the observations we already compute (health, drift, expectations). It
  shows the headline metrics with sparklines and deltas against an auto-picked baseline, plus **one** next step from
  the existing "next experiment" logic. Clicking a number opens its chart.
- **5 tabs instead of 16:**
  - **Summary**: the current Overview, slimmed, with the pipeline stages as a strip.
  - **Metrics**: Metrics, Plots and Uncertainty in one explorer.
  - **Review**: Expert review, Findings, Check my run and Precision & recall. One prioritised list, each item with its
    evidence chart inline.
  - **Files**: Config, Logs, Artifacts, Dataset, Compute, Setup and Sequences, as a left list and a right viewer, like
    a code editor.
  - **Timeline**.
- **Header:** two buttons (**Compare**, **Ask**) and `⋯`, which holds Report, Bundle, Dossier for the LLM, Start a
  study, Copy as `mf`, and Open in Azure ML studio.
- **Loading:** no panel. A 2 px progress line under the header, and sections show skeletons until their data arrives.
  "Load everything now" moves into `⋯`.
- **Stage** is a quiet selector next to the tabs (already done), remembered per pipeline.

### Today (home): the newest run answered, not a button to ask

- The top card is the verdict card for your newest run (the same component as the run page), computed in the
  background after sync. If the run is still running, it shows the live curve and "on track / diverging".
- Below that: **Needs attention** (failed or diverging), **Running now**, and **Continue** (a study, a comparison you
  left).
- The counter tiles go; the run count becomes a subtitle.

### Compare: the answer is a sentence too

- The top line reads, for example: "**Candidate is better on 7 of 9 metrics** (NDS +2.1%, mAP +0.8%); worse on
  loss/bbox (+8%, starting at step 14k). Changed: lr 2e-4 → 1e-4, temporal.num_frames 4 → 8."
- Charts overlay baseline (ghost) and candidate (solid) by default (already built for Plots).
- With 3+ runs selected, the same page becomes Progression.

## Craft details (the "it feels good" layer)

- **Motion:** 150 ms ease-out on panels and tabs; numbers count up once when they first appear; no motion when the
  system's reduce-motion setting is on.
- **Skeletons that match the final layout**, so nothing jumps when data arrives.
- **Undo instead of confirm:** removing a folder, unstarring or dismissing a finding shows a toast with Undo, not a
  dialog.
- **Keyboard everywhere:** `j/k` through runs, `c` compare with the selection, `.` opens the `⋯` menu, `?` shows all
  shortcuts. ⌘K handles "go to", "ask" and "run a command" in one box.
- **Paste anything:** paste an Azure ML studio URL, a run id or a local path into ⌘K or the search box and it opens.
- **Charts:** consistent palette (baseline grey and dashed, candidate accent), smoothing on with raw faint behind, and
  a crosshair that names the curve (already built).
- **Copy:** at most one short subtitle per card, written as an outcome ("Changes since the baseline"). Explanations
  move to a small ⓘ tooltip.
- **Visual system:** an 8-pt spacing grid, one card level (no cards inside cards), the accent colour only for the
  primary action and selection, and status colours only for status. Tag chips like `role=experiment` become quiet
  text; only `baseline` and `candidate` get a badge.
- **Empty states that do the work:** instead of "No baseline selected", pick one and say why ("auto-picked:
  role=baseline in the same experiment · change").

## Developer delight (things that make people tell colleagues)

- **Copy as `mf …`** on every view, and **Copy link** that restores the exact state (run, baseline, stage, tab,
  picked metrics).
- **Watch a running run:** a desktop notification when a live run diverges, NaNs or finishes, with the verdict in the
  notification.
- **Open in editor:** config keys and expectation checks link to `file:line` in the training repo (`vscode://` links),
  using the harness knowledge we already store.
- **PR-ready summary:** one click turns a comparison into a markdown block (verdict, table, two charts) for a PR or a
  Teams message.

## What we keep

The engine stays as it is: deterministic analyses, grounded AI, the observation contract, stage scoping, caching,
read-only Azure access. This redesign changes only how answers are presented and how many doors lead to them.

## Plan

| Wave | What | Size | Risk |
|---|---|---|---|
| **1. Calm the run page** ✅ | 5 tabs (merge views, no logic changes), 2 header buttons + `⋯`, loading panel → progress line, card subtitles trimmed, empty "What changed?" auto-picks a baseline | M | Low: same components regrouped; e2e journeys updated |
| **2. Verdict card** ✅ | `GET /runs/{id}/verdict` from existing observations + headline + next experiment; shown on the run page, Today and Compare | M | Low: built on existing analyses |
| **3. Navigation to 5** | Merge Local runs → Runs, Progression → Compare, Ledger/Test Space/Reports → Studies, AI → Ask; redirects keep old URLs working; remove Simple/Standard/Expert | M | Medium: muscle memory; old URLs redirect |
| **4. Craft pass** | Motion, skeletons, undo toasts, shortcuts sheet, paste-anything, copy-as-`mf`, visual tokens | M | Low |
| **5. Delight** | Run watch notifications, open-in-editor, PR summary | S–M each | Low |

Recommended order: 1 → 2 → 3, then 4 and 5 interleaved. Each wave ships on its own, with the e2e journeys moved to the
new shape in the same commit.

## What was built (waves 1 and 2)

* **Run page:** five places (Summary · Metrics · Review · Files · Timeline); a place with several views shows them as a
  quiet second row and remembers the last one. `?tab=` still names the view, so old links open the same thing.
  Header: **Compare**, **Ask** (a drawer beside the view, Esc closes) and `⋯` (Analyze, Report, Start a study, Dossier
  for the LLM, debug bundle, Copy link, Copy as `mf …`, Open in Azure ML studio, Load everything now, Loading details).
  The loading panel became a 2 px progress line; its details are in `⋯` (and shown when a part fails).
* **Verdict** (`application/verdict.py`, `GET /runs/{run}/verdict`, `mf verdict`): state, one sentence, the lead
  number against the baseline, the top problem, up to three headline numbers (value, sparkline, delta, good / bad /
  within noise from the metric registry's meaningful change), the baseline picked for you with its reason (tagged
  `role=baseline`, else the previous completed run of the experiment, same kind first, never the run's own stages),
  and one next step from the observation that comes first. Only from the observations the Review shows.
  `cached_only` answers from what is loaded; a local run is read at once, a remote one on one click ("Read the run").
* **Today:** the newest run's verdict at the top; the four counter tiles became one line of counts.
* **Compare:** `GET /compare/verdict` — "better on N of M metrics (headline metrics named first); worse on …" and
  what changed in the config, right under the run pickers.
* Copy trimmed on the Summary (pipeline stages, What changed, comparison idle); the "Check this run" strip is replaced
  by the verdict.
