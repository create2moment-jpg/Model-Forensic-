"""AI evaluation harness: fixed questions on the demo runs, scored on grounding — for any configured model.

Measures per question whether the answer parsed, how many claims passed the grounding checker, how many numbers
were not in the cited evidence (hallucinated numbers) and how many citations were invalid. ``mf ai-eval`` writes
the table to docs/ai-eval.md and exits non-zero when the grounded share falls below a threshold (CI).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

QUESTIONS: tuple[tuple[str, str | None, str], ...] = (
    (
        "local:cand-radar-8f",
        "local:baseline-cl4f-s0",
        "Did the candidate regress, where, and which config change is the most likely cause?",
    ),
    ("local:cand-radar-8f", None, "Is the radar branch learning? Cite the update ratios."),
    ("local:xf-pipe-finetune", None, "Can the 'best' checkpoint of this pipeline be trusted?"),
    ("local:xf-pipe-finetune", "local:xf-online-baseline", "Is the GT finetune worse than the online baseline?"),
    ("local:xf-pipe-finetune", None, "Should we train longer? Use the plateau and the learning rate."),
    ("local:xf-pipe-finetune", None, "Which loss family dominates and which heads are muted?"),
    ("local:xf-pipe-finetune", None, "Where does the model fail — which classes, distances or scenes?"),
    ("local:xf-sparse-uw", None, "Is any task head being abandoned by the learned weighting?"),
    ("local:lee-stream-unc", None, "Is the uncertainty head calibrated?"),
    ("local:lee-stream-unc", None, "Does the temporal memory help?"),
    ("local:baseline-cl4f-s0", None, "Summarise the health of this run in three points."),
    ("local:xf-pipe-finetune", None, "What changed between the pretrain and the finetune evaluation?"),
)


@dataclass
class EvalRow:
    run: str
    question: str
    ok: bool
    claims: int
    grounded: int
    bad_numbers: int
    bad_citations: int
    insufficient: bool
    seconds: float
    error: str = ""

    @property
    def grounded_share(self) -> float:
        return self.grounded / self.claims if self.claims else 0.0


def run_eval(container: Any, questions: tuple[tuple[str, str | None, str], ...] = QUESTIONS) -> list[EvalRow]:
    inv = container.investigator()
    rows = []
    for run, base, q in questions:
        t0 = time.perf_counter()
        try:
            res = inv.investigate(run, q, baseline=base)
            ans = res.answer
            rows.append(
                EvalRow(
                    run,
                    q,
                    ans.parse_error is None,
                    len(ans.claims),
                    sum(c.verified for c in ans.claims),
                    sum(1 for c in ans.claims for p in c.problems if p.startswith("number")),
                    sum(1 for c in ans.claims for p in c.problems if "evidence" in p),
                    ans.insufficient_evidence,
                    time.perf_counter() - t0,
                )
            )
        except Exception as exc:
            rows.append(
                EvalRow(run, q, False, 0, 0, 0, 0, True, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}")
            )
    return rows


def summary(rows: list[EvalRow]) -> dict[str, float]:
    claims = sum(r.claims for r in rows) or 1
    return {
        "questions": len(rows),
        "answered": sum(r.ok for r in rows),
        "claims": sum(r.claims for r in rows),
        "grounded_share": sum(r.grounded for r in rows) / claims,
        "bad_numbers": sum(r.bad_numbers for r in rows),
        "bad_citations": sum(r.bad_citations for r in rows),
        "mean_seconds": sum(r.seconds for r in rows) / max(1, len(rows)),
    }


def markdown(model: str, rows: list[EvalRow]) -> str:
    s = summary(rows)
    out = [
        f"# AI evaluation — `{model}`",
        "",
        f"{int(s['answered'])}/{int(s['questions'])} answered · {int(s['claims'])} claims · "
        f"**{s['grounded_share']:.0%} grounded** · {int(s['bad_numbers'])} hallucinated numbers · "
        f"{int(s['bad_citations'])} invalid citations · {s['mean_seconds']:.1f} s per question",
        "",
        "| Run | Question | Claims | Grounded | Bad numbers | Bad citations | Insufficient | s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    out += [
        f"| `{r.run}` | {r.question} | {r.claims} | {r.grounded} | {r.bad_numbers} | {r.bad_citations} | "
        f"{'yes' if r.insufficient else ''} | {r.seconds:.1f} |" + (f" {r.error}" if r.error else "")
        for r in rows
    ]
    out += [
        "",
        "Regenerate with `mf demo && mf ai-eval --out docs/ai-eval.md` (with the model configured in Settings / "
        "model-forensic.yaml). Unverified claims are never shown as fact in the UI or the study book.",
    ]
    return "\n".join(out) + "\n"
