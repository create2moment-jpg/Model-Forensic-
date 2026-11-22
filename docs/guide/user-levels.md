# User levels and features: a light view or the full tool

Every feature can be switched on or off: **Settings → Features** (or the slider icon next to the level switch in the
sidebar). Your **level** sets the defaults; single switches override them; presets: *Light view* (only features that
load nothing extra), *Level defaults*, *Everything*. Choices are kept per browser.

Two ways to make a page quieter — they differ:

| | Fold a section (its head) | Switch a feature off |
|---|---|---|
| What you see | the section's title stays | the section / tab is gone |
| What loads | everything (it is only hidden) | nothing for that feature |
| Use for | reading one part at a time | a light, fast view; a focused workspace |

Every feature carries its cost: **light** (nothing extra), **medium** (one more request when shown), **heavy**
(reads the run's full metric history or many series — the training health questions, slice breakdowns, findings,
uncertainty, sequences).

## What separates the levels

| | Simple (first steps) | Standard (intermediate) | Expert |
|---|---|---|---|
| **Question asked** | "Did my run work?" | "What is wrong, and what do I try next?" | "Why — which mechanism, and is the effect real?" |
| **Reads** | the overview, headline metrics, the curves, configs and logs | losses per term, filters by class / modality / distance, slice breakdowns, findings | per-layer decoder behaviour, learned task weights, timeline and compute, raw dimensions |
| **Compares** | two runs, Deltas & config | several candidates, run groups, Setup side by side, Plots | uncertainty against a baseline, layer-by-layer differences, significance with replicates |
| **Acts on** | Check my run's verdict | the training health questions' *what to try*, the next single change | experiment design (Test Space), knowledge from the code (harness tasks), study books |
| **Features on by default** | sets, metric lists, charts; Overview / Metrics / Config / Logs / Artifacts tabs | + filter, health questions, section summary, slices, Explain; Plots, Setup, Findings, Precision & recall, Sequences, Dataset, AI tabs; Compare Plots / Setup / Findings | + decoder-layer readout; Timeline, Uncertainty, Compute tabs; Compare Uncertainty |
| **Pages** | Check my run, Command center, Runs | + Studies, Progression, Compare, AI, Knowledge, Prefetch, Test Space, Library, Reports | same pages, every feature on them |

In practice the **intermediate** user diagnoses *what* is sub-optimal from the answers the tool gives (questions,
findings, deltas) and changes one thing at a time; the **expert** questions the answers themselves — reads the
mechanism (layers, weighting, query statistics, compute), checks whether a difference is larger than seed noise,
and writes down what the team learned (knowledge, study book).

The level is a default, not a gate: an intermediate user can switch on the decoder-layer readout alone, an expert
can work in the light view when a workspace is slow.
