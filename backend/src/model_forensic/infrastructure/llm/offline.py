"""Deterministic offline LLM used in demo mode and tests.

It does not reason; it turns the highest-severity findings in the context into labelled claims
with correct citations, so the full investigation pipeline (tools, grounding, persistence, UI)
works without an API key. It is clearly labelled as such everywhere it appears.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from model_forensic.application.ports import ChatMessage, LLMResponse, ToolSpec

_FINDING = re.compile(
    r"^\[(E\d+)\] \(finding\) FINDING \S+ \[(\w+)/(\w+)/conf=(\w+)\] (.+?): (.+?) \(evidence: ([E\d, ]+)\)", re.M
)
_HYP = re.compile(r"Deterministic hypotheses: (.+?)(?: Limitations:|$)")


class OfflineHeuristicLLM:
    model = "offline-heuristic (no LLM)"
    offline = True  # application.ports.is_offline

    def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] | None = None, *, json_mode: bool = False
    ) -> LLMResponse:
        ctx = next((m.content for m in messages if m.role == "user"), "")
        claims = []
        sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
        matches = sorted(_FINDING.finditer(ctx), key=lambda m: -sev_rank.get(m.group(2), 0))
        chosen = matches[:4] + [m for m in matches[4:] if m.group(3) == "hypothesized"][:2]
        for m in chosen:
            fid, _sev, label, conf, title, stmt, evs = m.groups()
            ev_ids = [e.strip() for e in evs.split(",") if e.strip()]
            ctype = (
                "HYPOTHESIS"
                if label == "hypothesized"
                else ("FACT" if label in {"computed", "observed"} else "INFERENCE")
            )
            claims.append(
                {"type": ctype, "text": f"{title}: {stmt}".strip(), "evidence": [fid, *ev_ids], "confidence": conf}
            )
            line_end = ctx.find("\n", m.end())
            hyp = _HYP.search(ctx[m.start() : line_end if line_end > 0 else None])
            if hyp:
                first = hyp.group(1).split("; ")[0]
                claims.append({"type": "HYPOTHESIS", "text": first, "evidence": [fid], "confidence": "low"})
        if not claims:
            body = {"summary": "Insufficient evidence.", "insufficient_evidence": True, "claims": []}
        else:
            body = {
                "summary": f"{len(matches)} deterministic finding(s); top: "
                + "; ".join(m.group(5) for m in matches[:3]),
                "insufficient_evidence": False,
                "claims": claims,
            }
        return LLMResponse(content=json.dumps(body), tool_calls=[], model=self.model)
