"""Grounding checks for AI output (anti-hallucination).

The model must answer as JSON claims, each with an epistemic label and cited evidence ids.
We then verify, deterministically:

1. every cited id exists in the context;
2. FACT/INFERENCE claims cite at least one id;
3. every number in a claim appears (within tolerance) in the cited evidence, or anywhere in the
   context for HYPOTHESIS/RECOMMENDATION claims; percentages are matched in both forms.

Claims that fail are kept but flagged ``unverified`` and shown as such; they are never silently
presented as facts. If no FACT/INFERENCE claim survives, the answer is "Insufficient evidence."
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from model_forensic.ai.context import InvestigationContext

_NUM_RE = re.compile(r"(?<![A-Za-z0-9_\-.])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?%?")
# Numbers that are structural, not measurements (ordinal "3 hypotheses", years, etc.) are allowed.
_ALLOWED_SMALL_INTS = set(range(0, 11))


class ClaimType(str, Enum):
    FACT = "FACT"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"
    RECOMMENDATION = "RECOMMENDATION"


@dataclass
class Claim:
    type: ClaimType
    text: str
    evidence: list[str]
    confidence: str | None = None
    verified: bool = True
    problems: list[str] = field(default_factory=list)


@dataclass
class GroundedAnswer:
    summary: str
    claims: list[Claim]
    insufficient_evidence: bool
    raw: str
    parse_error: str | None = None

    @property
    def verified_ratio(self) -> float:
        return sum(c.verified for c in self.claims) / len(self.claims) if self.claims else 0.0


def _numbers(text: str) -> list[tuple[float, bool]]:
    out = []
    for m in _NUM_RE.finditer(text):
        tok = m.group(0)
        pct = tok.endswith("%")
        try:
            out.append((float(tok.rstrip("%")), pct))
        except ValueError:
            continue
    return out


def _matches(x: float, pct: bool, pool: list[float]) -> bool:
    cands = [x, x / 100] if pct else [x]
    for c in cands:
        for p in pool:
            if p == 0 and c == 0:
                return True
            if p != 0 and math.isclose(c, p, rel_tol=0.051, abs_tol=0.0006):
                return True
            # Allow rounding to displayed precision (e.g. 0.6177 cited as 0.62).
            if p != 0 and abs(c - p) <= 0.5 * 10 ** (-_decimals(c)) + 1e-12:
                return True
    return False


def _decimals(x: float) -> int:
    s = f"{x:.10g}"
    return len(s.split(".")[1]) if "." in s else 0


def _extract_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    obj: dict[str, Any] = json.loads(raw[start : end + 1])
    return obj


def ground(raw: str, ctx: InvestigationContext) -> GroundedAnswer:
    try:
        obj = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return GroundedAnswer(
            summary="Insufficient evidence.",
            claims=[],
            insufficient_evidence=True,
            raw=raw,
            parse_error=f"model did not return valid JSON: {exc}",
        )
    by_id = ctx.by_id()
    all_numbers = ctx.numbers()
    claims: list[Claim] = []
    for c in obj.get("claims", []) or []:
        try:
            ctype = ClaimType(str(c.get("type", "")).upper())
        except ValueError:
            ctype = ClaimType.HYPOTHESIS
        cl = Claim(
            type=ctype,
            text=str(c.get("text", "")),
            evidence=[str(e) for e in c.get("evidence", []) or []],
            confidence=c.get("confidence"),
        )
        unknown = [e for e in cl.evidence if e not in by_id]
        if unknown:
            cl.problems.append(f"cites unknown evidence ids {unknown}")
        if ctype in (ClaimType.FACT, ClaimType.INFERENCE) and not [e for e in cl.evidence if e in by_id]:
            cl.problems.append("factual claim without valid evidence citation")
        pool = [n for e in cl.evidence if e in by_id for n in by_id[e].numbers]
        if ctype in (ClaimType.HYPOTHESIS, ClaimType.RECOMMENDATION):
            pool = pool + all_numbers
        for x, pct in _numbers(cl.text):
            if not pct and x in _ALLOWED_SMALL_INTS:
                continue
            if not _matches(x, pct, pool):
                cl.problems.append(f"number {x:g}{'%' if pct else ''} not found in cited evidence")
        cl.verified = not cl.problems
        claims.append(cl)
    insufficient = bool(obj.get("insufficient_evidence")) or not any(
        c.verified and c.type in (ClaimType.FACT, ClaimType.INFERENCE) for c in claims
    )
    summary = str(obj.get("summary") or "")
    if insufficient and not summary:
        summary = "Insufficient evidence."
    return GroundedAnswer(summary=summary, claims=claims, insufficient_evidence=insufficient, raw=raw)
