"""Question templates for the study wizard: what to claim, what decides it, what to rule out first.

The confounders encode the lessons of a real training study: compare on one ruler, check that the ground truth
of a scored attribute varies, confirm validation exists, change one thing at a time, and read plateaus with the
learning rate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class QuestionTemplate:
    id: str
    title: str
    question: str
    claim: str  # with {candidate}, {baseline}, {metric} placeholders
    falsified_if: str
    confounders: tuple[str, ...]
    analyses: tuple[str, ...]
    needs_baseline: bool = True
    default_metric: str = "val/NDS"
    hint: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


COMMON = (
    "Same combined-metrics ruler for every compared run (P1)",
    "Same evaluation split / data version",
)
TEMPLATES: tuple[QuestionTemplate, ...] = (
    QuestionTemplate(
        "change_better",
        "Is my change better?",
        "Does the change in {candidate} improve the model compared with {baseline}?",
        "{candidate} improves {metric} over {baseline} beyond run-to-run noise",
        "{metric} is within the noise floor of {baseline}, or worse; or the gain only appears on a different ruler",
        (
            *COMMON,
            "Only one meaningful config change between the runs",
            "Seed noise known (≥ 2 baseline replicates)",
            "Same training length and schedule",
        ),
        ("regression", "config_attribution", "eval_localization", "study_checks"),
        hint="Pick the run with your change as candidate and the last good run as baseline.",
    ),
    QuestionTemplate(
        "why_regressed",
        "Why did it regress?",
        "Which difference between {baseline} and {candidate} explains the drop in {metric}?",
        "The regression of {metric} in {candidate} is caused by <config change>",
        "Reverting only that change does not recover {metric}; or the drop is within noise",
        (*COMMON, "The drop is beyond the noise floor", "Data / environment unchanged between the runs"),
        ("regression", "config_attribution", "stage_delta", "sequence_drilldown", "study_checks"),
    ),
    QuestionTemplate(
        "train_longer",
        "Should I train longer?",
        "Would more epochs improve {candidate}?",
        "Training {candidate} longer would improve {metric}",
        "The loss plateaued while the learning rate was still high (converged, P8), or only σ is still moving",
        (
            "Learning-rate schedule available (logged or metrics.csv)",
            "Validation metrics logged during training (P5)",
            "Total loss vs network-facing loss compared (thermostat)",
        ),
        ("study_checks", "training_dynamics", "loss_ledger"),
        needs_baseline=False,
        default_metric="train/loss",
    ),
    QuestionTemplate(
        "head_useful",
        "Is this head / branch useful?",
        "Does <head> contribute to {metric} in {candidate}?",
        "<head> contributes to {metric}",
        "Its loss is self-muted by σ and an ablation without it does not change {metric}",
        (
            "Muted auxiliary head vs muted sensor kept apart (P7)",
            "Ablation or run without the head available",
            "Gradient share checked, not only loss share",
        ),
        ("loss_ledger", "task_weighting", "modality_ablation", "loss_interactions", "study_checks"),
        needs_baseline=False,
        default_metric="val/NDS",
    ),
    QuestionTemplate(
        "pretrain_finetune",
        "Pretrain vs finetune",
        "What did the finetune stage of {candidate} change compared with pretraining?",
        "Finetuning improves {metric} over the pretrained model",
        "The evaluation after finetuning is not better per class / distance, or the gain is on a degenerate attribute",
        (
            *COMMON,
            "Ground truth of every scored attribute varies on the eval split (P3)",
            "Both evaluations use the same split",
        ),
        ("stage_delta", "eval_localization", "sequence_drilldown", "loss_ledger", "study_checks"),
        needs_baseline=False,
        default_metric="val/score",
    ),
)
BY_ID = {t.id: t for t in TEMPLATES}
MEASUREMENT_CHECKS = ("P1", "P2", "P3", "P6")  # a failure here means the numbers themselves are wrong
