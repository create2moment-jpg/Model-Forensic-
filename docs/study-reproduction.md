# Reproducing a hand-written study

The 1V5R study (GT pretrain + finetune vs the online autolabel baseline) was written by hand with a local LLM. This
page records, conclusion by conclusion, whether `mf study book` reaches the same conclusion **deterministically** from
run ids — on the sanitised demo that mirrors its layout (`local:xf-pipe-finetune` vs `local:xf-online-baseline`) —
and which check or chapter produces it. Regression tests: `backend/tests/unit/test_study_reproduction.py`,
`test_study_book.py`.

| # | Hand-written conclusion | Book | Produced by |
|---|---|---|---|
| V | The 0.40 vs 0.55 gap is a **ruler artifact**; on the shared ruler the GT models win | ✅ reproduced | P1 + *The shared ruler* chapter, verdict up front |
| 1 | Training is healthy but **plateaus at ~epoch 2 of 8 at high LR**; ~6 epochs wasted | ✅ reproduced | P8 now states the plateau in epochs (logged `epoch` series, else `max_epochs` from the config) and the epochs after it |
| 1b | The online run's extra epochs still buy loss (schedule disadvantage, not the gap's cause) | ✅ reproduced | *Convergence → Epoch budget across runs*: epochs per run and the loss each longer run still bought after the shortest run's budget, with a verdict (real schedule advantage vs not what separates the runs) |
| 2 | Finetune looks useless on mAP but **improves localisation and tracking** | ✅ reproduced (when those metrics are logged per stage) | *What each stage changed*: change by metric family (headline / detection / localisation / tracking / velocity) + the "looks useless on the headline ruler but…" verdict |
| 2b | The finetune's **velocity gain is an artifact** of zeroed manual-label GT velocity | ✅ reproduced | P3 degenerate GT → the velocity row is marked *not trustworthy* and the verdict says so |
| 2c | The finetune is a **budget reallocation** (radar share 59 → 67 % while box losses collapse) | ✅ reproduced (pipelines with ≥ 2 training stages) | *The later stage — budget reallocation*: ledger per training stage, family shares and shift in points, "absolute loss nearly unchanged — the share moved because others shrank" |
| 3 | **No validation metrics** — checkpoint selection is blind | ✅ reproduced | P5 FAIL + P4 (monitor never logged) |
| 4 | **Not capacity-bound**; radar aux head self-muted by σ (the modality stays live) | ✅ reproduced | new *Capacity* chapter: plateau at high LR, self-muted head (P7), decoder saturation → *REJECTED (provisionally)* + decisive experiments (overfit one batch, capacity ladder, val gap) |
| 4b | Query budget 5× surplus (1216 queries vs ~256 detections) | ❌ missing | needs detections-per-scene from the evaluation outputs; not in the current readers |
| 5 | Dominant weakness: **far-range VRU**, shared with the online model | ✅ reproduced | *Where the model fails* (AUC by distance per class, both runs) |
| 6 | **FP tails**: a few pathological scenes dominate | ✅ reproduced | *Where the model fails* + sequence drill-down |
| 7 | Loss anatomy: σ is trust not share; negative weighted loss is the σ tax | ✅ reproduced | *The loss ledger* chapter |
| 8 | Denoising scaffold at its noise floor | ✅ reproduced | decoder/denoising analyses ("Denoising task is trivial") |
| 9 | Trifocal ≈ monofocal on the shared ruler (more input did not help) | 🟡 partly | possible with a 3-run study (shared-ruler table); not yet turned into a capacity signal automatically |

**Not reproducible by design:** the narrative prose, the hand-picked figures, and judgement calls such as which
experiment to run first this week. With `--ai` the book adds a grounded narrative per chapter (only claims that cite
the chapter's evidence and use its numbers survive).

**Open (small):** queries-vs-detections surplus (4b), "more input did not
help" as an automatic capacity signal from multi-run studies (9).
