# AI evaluation — `offline-heuristic (no LLM)`

12/12 answered · 71 claims · **100% grounded** · 0 hallucinated numbers · 0 invalid citations · 0.2 s per question

| Run | Question | Claims | Grounded | Bad numbers | Bad citations | Insufficient | s |
|---|---|---|---|---|---|---|---|
| `local:cand-radar-8f` | Did the candidate regress, where, and which config change is the most likely cause? | 8 | 8 | 0 | 0 |  | 0.1 |
| `local:cand-radar-8f` | Is the radar branch learning? Cite the update ratios. | 7 | 7 | 0 | 0 |  | 0.0 |
| `local:xf-pipe-finetune` | Can the 'best' checkpoint of this pipeline be trusted? | 5 | 5 | 0 | 0 |  | 0.7 |
| `local:xf-pipe-finetune` | Is the GT finetune worse than the online baseline? | 6 | 6 | 0 | 0 |  | 0.3 |
| `local:xf-pipe-finetune` | Should we train longer? Use the plateau and the learning rate. | 5 | 5 | 0 | 0 |  | 0.1 |
| `local:xf-pipe-finetune` | Which loss family dominates and which heads are muted? | 5 | 5 | 0 | 0 |  | 0.1 |
| `local:xf-pipe-finetune` | Where does the model fail — which classes, distances or scenes? | 5 | 5 | 0 | 0 |  | 0.1 |
| `local:xf-sparse-uw` | Is any task head being abandoned by the learned weighting? | 6 | 6 | 0 | 0 |  | 0.2 |
| `local:lee-stream-unc` | Is the uncertainty head calibrated? | 8 | 8 | 0 | 0 |  | 0.1 |
| `local:lee-stream-unc` | Does the temporal memory help? | 8 | 8 | 0 | 0 |  | 0.0 |
| `local:baseline-cl4f-s0` | Summarise the health of this run in three points. | 3 | 3 | 0 | 0 |  | 0.0 |
| `local:xf-pipe-finetune` | What changed between the pretrain and the finetune evaluation? | 5 | 5 | 0 | 0 |  | 0.1 |

Regenerate with `mf demo && mf ai-eval --out docs/ai-eval.md` (with the model configured in Settings / model-forensic.yaml). Unverified claims are never shown as fact in the UI or the study book.
