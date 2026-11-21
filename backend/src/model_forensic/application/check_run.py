""" "Check my run" — the beginner path: a safe set of analyses, at most five plain-language cards.

Each card: traffic light, one-sentence meaning, why it matters, what to do next, and an expandable "learn more"
with the evidence and the jargon. No AI involved; everything comes from the deterministic analyses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from model_forensic.application.service import ForensicService
from model_forensic.domain.findings import Finding

SAFE_ANALYSES = (
    "training_dynamics",
    "study_checks",
    "task_weighting",
    "loss_ledger",
    "eval_localization",
    "sequence_drilldown",
    "stage_delta",
    "metric_coverage",
    "regression",
)
MAX_CARDS = 5

# Plain-language framing per analysis: (why it matters, default next step, glossary terms)
FRAMING: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "training_dynamics": (
        "If training itself is unhealthy, every metric after it is unreliable.",
        "Look at the loss curve around the step named in the evidence; check learning rate and data.",
        ("loss", "learning rate", "divergence"),
    ),
    "study_checks": (
        "This is about whether the numbers can be trusted at all — not about the model.",
        "Fix the setup issue first (see what to do), then re-read the results.",
        ("ruler", "checkpoint monitor", "validation", "plateau"),
    ),
    "task_weighting": (
        "The model decides itself how much each task counts; it may be quietly ignoring one.",
        "Compare the affected task's own loss with its learned weight; consider fixing the weight.",
        ("learned task weight", "log sigma"),
    ),
    "loss_ledger": (
        "A large share of the loss is not the same as a large influence on learning.",
        "Judge each part of the loss by how it changes and how much the model trusts it.",
        ("loss ledger", "log sigma", "sigma tax"),
    ),
    "eval_localization": (
        "Knowing *where* the model fails tells you what to change.",
        "Inspect the named distances or scenes before changing the model.",
        ("AUC", "false positive", "distance bucket"),
    ),
    "sequence_drilldown": (
        "A few bad recordings can dominate an average.",
        "Open the worst sequences listed in the evidence and look for a common condition.",
        ("sequence", "false positive", "precision"),
    ),
    "stage_delta": (
        "A later training stage can quietly undo what an earlier one learned.",
        "Check the regressed classes or distances in the later stage's data and settings.",
        ("stage", "pretrain / finetune"),
    ),
    "metric_coverage": (
        "You cannot conclude anything from a metric that was never logged.",
        "Add the missing logging to the training config before the next run.",
        ("metric family",),
    ),
    "regression": (
        "Compared with the baseline, something got worse beyond normal run-to-run noise.",
        "Look at the named slice; test the config change that is the most likely cause on its own.",
        ("baseline", "regression", "noise floor"),
    ),
}

GLOSSARY: dict[str, str] = {
    "loss": "The number training tries to make small. Falling = the model is learning the training data.",
    "learning rate": "How big each training step is. Too high: unstable; too low or decayed to zero: learning stops.",
    "divergence": "The loss suddenly grows or becomes NaN — training broke. Usually learning rate or bad data.",
    "plateau": "The loss stops improving. Read it with the learning rate: still high = done; near zero = schedule ran out.",
    "validation": "Metrics on data the model does not train on. Without them you cannot pick the best checkpoint.",
    "checkpoint monitor": "The metric used to pick the 'best' saved model. If it is never logged, 'best' is silently the last.",
    "ruler": "The formula that turns many metrics into one headline score. Two runs with different rulers are not comparable.",
    "learned task weight": "With several tasks, the model learns how much each counts. A falling weight = it trusts that task less.",
    "log sigma": "The learned uncertainty of a task. Higher log σ = lower weight (weight ≈ e^(−2·log σ)).",
    "sigma tax": "With learned weights the logged loss includes +log σ, which can be negative — that is expected, not a bug.",
    "loss ledger": "A table of every part of the loss: its raw size, its weighted size and how much it actually drives learning.",
    "AUC": "Area under the precision-recall curve: one number for detection quality (1 = perfect).",
    "false positive": "The model reports an object that is not there.",
    "precision": "Of everything the model reported, the share that was real.",
    "recall": "Of everything that was really there, the share the model found.",
    "distance bucket": "Results grouped by how far the object is from the vehicle; far objects are usually harder.",
    "sequence": "One recording (a drive). Results per sequence show whether problems cluster in a few scenes.",
    "stage": "One step of a training pipeline (e.g. pretrain, finetune, evaluate).",
    "pretrain / finetune": "Pretrain learns general features on lots of data; finetune adapts them on targeted data.",
    "metric family": "A group of related metrics (e.g. all validation metrics) a stage is expected to log.",
    "baseline": "The reference run you compare against, usually the last good model.",
    "regression": "A metric got worse compared with the baseline.",
    "noise floor": "How much a metric changes between identical runs with different seeds; smaller changes are noise.",
}


CHECKS_PLAIN: dict[str, tuple[str, str]] = {
    "P1": (
        "Runs are scored with different formulas",
        "Compare per-class metrics instead of the headline score, or re-score both runs with one ruler.",
    ),
    "P2": (
        "A metric does not mean what its name suggests",
        "Read the glossary note before drawing conclusions from it.",
    ),
    "P3": (
        "Some ground truth is constant, so its error metric is meaningless",
        "Ignore the named error metric for this evaluation set, or evaluate on data where it varies.",
    ),
    "P4": (
        "The 'best' checkpoint is not really the best",
        "Set best_checkpoint_monitor to a metric that is actually logged.",
    ),
    "P5": (
        "Training has no validation metrics",
        "Enable the validation loop so checkpoints are chosen on held-out data.",
    ),
    "P6": ("A figure cannot be traced to its data", "Regenerate the figure from a named source."),
    "P7": (
        "A muted helper head is not a muted sensor",
        "When reporting, say whether you mean the auxiliary head or the sensor input.",
    ),
    "P8": (
        "Training stopped improving early",
        "Do not just train longer; change data, loss weights or capacity — or stop earlier to save compute.",
    ),
}


@dataclass
class Card:
    light: str  # red | amber | green
    title: str
    meaning: str
    why: str
    todo: str
    learn_more: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    analysis: str = ""
    severity: str = "info"


def _first_sentence(text: str) -> str:
    for sep in (". ", ": ", "; "):
        if sep in text:
            return text.split(sep, 1)[0].rstrip(".") + "."
    return text


def card_for(f: Finding, analysis: str) -> Card:
    why, todo, terms = FRAMING.get(analysis, ("", "Open the finding in the Findings tab for details.", ()))
    if f.recommendations:
        todo = f"{f.recommendations[0].title} — expected: {f.recommendations[0].expected_outcome}"
    light = "red" if f.severity.rank >= 3 else "amber" if f.severity.rank >= 2 else "green"
    title = f.title
    cid = f.title.split(" ", 1)[0]
    if analysis == "study_checks" and cid in CHECKS_PLAIN:
        title, todo = CHECKS_PLAIN[cid]
    learn = [e.statement for e in f.evidence[:3]] + [
        f"Possible cause (not established): {h.statement}" for h in f.hypotheses[:2]
    ]
    return Card(light, title, _first_sentence(f.statement), why, todo, learn, list(terms), analysis, f.severity.value)


def check_run(svc: ForensicService, run: str | None = None) -> dict[str, Any]:
    """The newest completed run if ``run`` is None; baseline picked automatically when one exists."""
    if run is None:
        res = svc.search("status:completed", limit=1)
        if not res.runs:
            raise ValueError("no completed runs indexed — run a sync first")
        ref = res.runs[0].ref
    else:
        ref = svc.ref(run)
    r = svc.get_run(ref)
    baseline = svc.suggest_baseline(ref)
    results = svc.analyze(ref, baseline, analyses=list(SAFE_ANALYSES), persist=False)
    scored = [(f, res.analysis) for res in results for f in res.findings]
    scored.sort(key=lambda t: (-t[0].severity.rank, SAFE_ANALYSES.index(t[1]) if t[1] in SAFE_ANALYSES else 99))
    cards = [card_for(f, a) for f, a in scored if f.severity.rank >= 1][:MAX_CARDS]
    if not cards:
        cards = [
            Card(
                "green",
                "No problems found",
                "The checks that could run found nothing worrying in this run.",
                "Healthy training and evaluation mean you can compare it with other runs with confidence.",
                "Pick a baseline and compare, or start a study for a deeper look.",
                [],
                ["baseline"],
            )
        ]
    ran = [x.analysis for x in results if x.skipped_reason is None]
    lights = [c.light for c in cards]
    overall = "red" if "red" in lights else "amber" if "amber" in lights else "green"
    return {
        "run": {"ref": str(r.ref), "name": r.display_name or r.name, "status": r.status.value, "user": r.user},
        "baseline": str(baseline) if baseline else None,
        "overall": overall,
        "summary": {
            "red": "Something needs attention before you trust this run.",
            "amber": "Usable, with things worth checking.",
            "green": "Looks healthy.",
        }[overall],
        "cards": [asdict(c) for c in cards],
        "more": max(0, sum(1 for f, _ in scored if f.severity.rank >= 1) - len(cards)),
        "checked": ran,
        "glossary": {t: GLOSSARY[t] for c in cards for t in c.terms if t in GLOSSARY},
    }
