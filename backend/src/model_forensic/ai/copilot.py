"""AI copilot features that stay grounded.

* ``narrate`` — a short narrative for one chapter of the study book from that chapter's own evidence; only claims
  that pass the grounding checker (cited evidence ids, numbers present in the cited evidence) are kept.
* ``ask_about_finding`` — an investigation focused on one finding (its statement and evidence are the question's
  context), answered by the regular grounded investigator.
* ``next_experiment`` — the next run as a config diff: rule-based when a finding's proposal names a config key
  (revert / ablate it), otherwise proposed by the model and validated — keys that are not in the run's config are
  rejected. Shown, never submitted.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml

from model_forensic.ai.context import EvidenceItem, InvestigationContext, extract_numbers
from model_forensic.ai.grounding import ClaimType, GroundedAnswer, ground
from model_forensic.ai.investigator import SYSTEM_RULES
from model_forensic.application.ports import ChatMessage, LLMProvider

_TAG = re.compile(r"<[^>]+>")


def evidence_from_html(body_html: str, limit: int = 40) -> list[str]:
    """Plain-text evidence lines of a rendered chapter (tables, findings, lists)."""
    text = _TAG.sub("\n", body_html.replace("</td>", " | ").replace("</th>", " | "))
    lines = [re.sub(r"\s+", " ", html.unescape(x)).strip(" |") for x in text.split("\n")]
    return [x for x in lines if len(x) > 12][:limit]


def _context(lines: Sequence[str]) -> InvestigationContext:
    items = [
        EvidenceItem(id=f"E{i + 1}", kind="evidence", text=t, numbers=extract_numbers(t)) for i, t in enumerate(lines)
    ]
    return InvestigationContext(items=items, untrusted_blocks=[], nonce="book", dropped_sections=[])


def narrate(llm: LLMProvider, chapter: str, lines: Sequence[str]) -> GroundedAnswer | None:
    if not lines:
        return None
    ctx = _context(lines)
    msgs = [
        ChatMessage(
            "system",
            "You write two to four sentences of narrative for one chapter of a training-forensics "
            'study, as JSON {"summary": str, "claims": [{"type": "FACT|INFERENCE|HYPOTHESIS|RECOMMENDATION", '
            '"text": str, "evidence": ["E1"]}]}.\n' + SYSTEM_RULES,
        ),
        ChatMessage("user", f"Chapter: {chapter}\n\n{ctx.render()}"),
    ]
    raw = llm.complete(msgs, json_mode=True).content or ""
    return ground(raw, ctx)


def narrative_html(ans: GroundedAnswer | None) -> str:
    if ans is None:
        return ""
    kept = [c for c in ans.claims if c.verified]
    dropped = len(ans.claims) - len(kept)
    if not kept:
        return (
            "<div class='ai'><span class='pill ghost'>AI</span> No narrative passed the grounding check"
            + (f" ({dropped} claim(s) removed)" if dropped else "")
            + ".</div>"
        )
    label = {
        ClaimType.FACT: "",
        ClaimType.INFERENCE: "Inference: ",
        ClaimType.HYPOTHESIS: "Hypothesis: ",
        ClaimType.RECOMMENDATION: "Recommendation: ",
    }
    body = " ".join(
        f"{label[c.type]}{html.escape(c.text)} <span class='cap'>[{', '.join(map(html.escape, c.evidence))}]</span>"
        for c in kept
    )
    return (
        "<div class='ai'><span class='pill ghost'>AI-generated · grounded</span> "
        + body
        + (f" <span class='cap'>({dropped} unverifiable claim(s) removed)</span>" if dropped else "")
        + "</div>"
    )


def focus_question(question: str, finding: Any) -> str:
    ev = "; ".join(e.statement for e in finding.evidence[:5])
    return (
        f"{question.strip() or 'Explain this finding and what to do about it.'}\n\nFocus on the finding "
        f"'{finding.title}': {finding.statement} (its evidence: {ev})"
    )


@dataclass
class ConfigChange:
    path: str
    current: Any
    proposed: Any
    why: str


@dataclass
class NextExperiment:
    source: str  # rule | ai | none
    title: str
    changes: list[ConfigChange] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    expected_outcome: str = ""
    measurements: list[str] = field(default_factory=list)

    def yaml_patch(self) -> str:
        nested: dict[str, Any] = {}
        for c in self.changes:
            cur = nested
            parts = c.path.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = c.proposed
        return yaml.safe_dump(nested, sort_keys=False) if nested else ""


def next_experiment(
    svc: Any, llm: LLMProvider | None, run: str, finding: Any, baseline: str | None = None, ai: bool = True
) -> NextExperiment:
    flat = svc.flat_config(run)
    prop = finding.recommendations[0] if finding.recommendations else None
    title = prop.title if prop else f"Follow-up on: {finding.title}"
    out = NextExperiment(
        "none",
        title,
        expected_outcome=prop.expected_outcome if prop else "",
        measurements=list(prop.measurements) if prop else [],
    )
    if prop is not None and prop.controlled_variable in flat and baseline:
        diff = svc.diff_configs(baseline, run)
        ch = next((c for c in diff.changes if c.path == prop.controlled_variable), None)
        if ch is not None:
            out.source = "rule"
            out.changes.append(
                ConfigChange(
                    ch.path, ch.candidate, ch.baseline, f"revert to the baseline value to test: {finding.title}"
                )
            )
            return out
    if not ai or llm is None:
        return out
    words = {w for w in re.findall(r"[a-z_]{3,}", f"{finding.title} {finding.statement} {title}".lower())}
    keys = [k for k in flat if any(w in k.lower() for w in words)][:60] or list(flat)[:60]
    menu = "\n".join(f"{k} = {json.dumps(flat[k], default=str)[:80]}" for k in keys)
    msgs = [
        ChatMessage(
            "system",
            "Propose the ONE next training experiment as a minimal config change. Answer JSON "
            '{"changes": [{"path": str, "value": any, "why": str}]} with 1-3 changes. Use ONLY paths from '
            "the list; never invent keys. Prefer changing one variable.",
        ),
        ChatMessage(
            "user",
            f"Finding: {finding.title}: {finding.statement}\nSuggested experiment: {title}\n\n"
            f"Config keys (path = current value):\n{menu}",
        ),
    ]
    raw = llm.complete(msgs, json_mode=True).content or ""
    try:
        start, end = raw.find("{"), raw.rfind("}")
        obj = json.loads(raw[start : end + 1]) if start >= 0 else {}
    except (ValueError, json.JSONDecodeError):
        obj = {}
    for c in (obj.get("changes") or [])[:3]:
        path = str(c.get("path", ""))
        if path not in flat:
            out.rejected.append(path or "(empty)")
            continue
        out.changes.append(ConfigChange(path, flat[path], c.get("value"), str(c.get("why", ""))[:300]))
    out.source = "ai" if out.changes else "none"
    return out
