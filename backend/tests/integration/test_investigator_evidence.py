"""Templates' `include` blocks must reach the model's context; extra runs must be included."""

from model_forensic.ai.investigator import Investigator
from model_forensic.application.ports import LLMResponse


class CapturingLLM:
    model = "capture"

    def __init__(self) -> None:
        self.context = ""

    def complete(self, messages, tools=None, *, json_mode=False) -> LLMResponse:
        self.context = next(m.content for m in messages if m.role == "user")
        return LLMResponse(content='{"summary": "s", "claims": []}', tool_calls=[], model=self.model)


def run_prompt(container, prompt_id: str, **kw):
    llm = CapturingLLM()
    inv = Investigator(container.service, llm, container.prompts, max_steps=0).investigate(prompt_id=prompt_id, **kw)
    return llm.context, inv


def test_camera_prompt_adds_config_and_curve_digests(container) -> None:
    ctx, inv = run_prompt(
        container,
        "cmp_camera_backbone",
        run="cand-radar-8f",
        baseline="baseline-cl4f-s0",
        variables={"backbone": "ConvNeXt-B"},
    )
    assert "config model.backbone:" in ctx and "candidate=resnet50" in ctx and "baseline=resnet50" in ctx
    assert "(series) local:cand-radar-8f train/loss" in ctx and "25%" in ctx
    assert "ConvNeXt-B" in inv.question and inv.prompt == "cmp_camera_backbone@1"
    # curve-digest numbers are citable and grounded
    assert any(e.kind == "series" and e.numbers for e in inv.context.items)


def test_radar_prompt_includes_fusion_config(container) -> None:
    ctx, _ = run_prompt(container, "cmp_radar_multiscale", run="cand-radar-8f", baseline="baseline-cl4f-s0")
    assert "config model.fusion.modalities:" in ctx and "radar" in ctx


def test_logs_prompt_adds_error_lines_as_untrusted(container) -> None:
    ctx, _ = run_prompt(container, "logs_triage", run="lr-sweep-1e3")
    assert '<untrusted source="error/warning lines of local:lr-sweep-1e3' in ctx
    assert "FloatingPointError" in ctx and '<untrusted source="log tail' in ctx


def test_multi_run_prompt_includes_other_runs(container) -> None:
    ctx, inv = run_prompt(
        container, "cmp_multi_users", run="baseline-cl4f-s0", others=["priya-lr3e4-wd05", "priya-bs32", "cand-radar-8f"]
    )
    for r in ("priya-lr3e4-wd05", "priya-bs32", "cand-radar-8f"):
        assert f"other run local:{r}" in ctx
    assert "config optimizer.weight_decay:" in ctx  # what priya changed vs the reference
    assert "priya-bs32" in inv.question  # {{run_ids}} rendered


def test_default_context_without_template_is_unchanged(container) -> None:
    llm = CapturingLLM()
    Investigator(container.service, llm, container.prompts, max_steps=0).investigate(
        "cand-radar-8f", "why?", baseline="baseline-cl4f-s0"
    )
    assert "(series)" not in llm.context and "log tail" not in llm.context and "error/warning lines" not in llm.context


def test_small_context_budget_sheds_digests_not_runs(container) -> None:
    llm = CapturingLLM()
    others = ["priya-lr3e4-wd05", "priya-bs32", "cand-radar-8f", "query-1800-spiky", "cand-uncert", "lr-sweep-1e3"]
    inv = Investigator(container.service, llm, container.prompts, max_steps=0, token_budget=2000).investigate(
        "baseline-cl4f-s0", prompt_id="cmp_multi_users", others=others
    )
    assert len(llm.context) // 4 < 2600  # near budget (rendering adds headers)
    assert all(f"other run local:{r}" in llm.context for r in others)  # run identities survive
    assert any("curve digests" in d for d in inv.context.dropped_sections)
    assert "Context truncated" in llm.context
