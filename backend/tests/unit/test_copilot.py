"""AI copilot: grounded chapter narratives, ask-about-a-finding, next-experiment diff, eval harness."""

import json

from fastapi.testclient import TestClient

from model_forensic.ai import copilot
from model_forensic.ai.eval_harness import markdown, run_eval, summary
from model_forensic.api.app import create_app
from model_forensic.application.ports import LLMResponse


class Scripted:
    model = "scripted"

    def __init__(self, reply: dict) -> None:
        self.reply = reply

    def complete(self, messages, tools=None, *, json_mode=False):
        return LLMResponse(content=json.dumps(self.reply), tool_calls=[], model=self.model)


def test_narrative_keeps_only_grounded_claims() -> None:
    lines = [
        "P1 FAIL ruler mismatch between box3d_ground_truth and box3d_sequential",
        "candidate better in 25 of 40 class metric cells",
    ]
    llm = Scripted(
        {
            "summary": "s",
            "claims": [
                {"type": "FACT", "text": "The candidate wins 25 of 40 cells.", "evidence": ["E2"]},
                {"type": "FACT", "text": "Headline improved by 37%.", "evidence": ["E2"]},  # invented number
                {"type": "FACT", "text": "Rulers differ.", "evidence": ["E9"]},  # unknown citation
            ],
        }
    )
    ans = copilot.narrate(llm, "Checks", lines)
    html = copilot.narrative_html(ans)
    assert "25 of 40" in html and "37%" not in html and "2 unverifiable claim(s) removed" in html
    assert copilot.narrate(llm, "x", []) is None
    assert copilot.evidence_from_html("<table><tr><td>P1</td><td>FAIL ruler mismatch</td></tr></table>")


def _attribution_finding(container):
    res = container.service.analyze("local:cand-radar-8f", "local:baseline-cl4f-s0", analyses=["config_attribution"])
    return next(f for r in res for f in r.findings if f.recommendations)


def test_next_experiment_rule_and_ai(container) -> None:
    f = _attribution_finding(container)
    ne = copilot.next_experiment(container.service, None, "local:cand-radar-8f", f, "local:baseline-cl4f-s0")
    assert (
        ne.source == "rule" and len(ne.changes) == 1 and ne.changes[0].path == f.recommendations[0].controlled_variable
    )
    assert ne.yaml_patch().strip()
    key = next(iter(container.service.flat_config("local:cand-radar-8f")))
    llm = Scripted({"changes": [{"path": key, "value": 1, "why": "test"}, {"path": "model.invented.key", "value": 2}]})
    ne2 = copilot.next_experiment(container.service, llm, "local:cand-radar-8f", f)
    assert ne2.source == "ai" and [c.path for c in ne2.changes] == [key] and ne2.rejected == ["model.invented.key"]


def test_api_ask_and_next(container) -> None:
    f = _attribution_finding(container)
    c = TestClient(create_app(container))
    inv = c.post(f"/api/v1/findings/{f.id}/ask", json={"question": "Why?"}).json()
    assert inv["id"] and f.title in inv["question"]
    ne = c.post(f"/api/v1/findings/{f.id}/next-experiment").json()
    assert ne["source"] == "rule" and ne["yaml"]


def test_eval_harness_offline(container) -> None:
    rows = run_eval(container, (("local:cand-radar-8f", "local:baseline-cl4f-s0", "Did it regress?"),))
    s = summary(rows)
    assert s["answered"] == 1 and s["bad_numbers"] == 0 and "grounded" in markdown("offline", rows)
