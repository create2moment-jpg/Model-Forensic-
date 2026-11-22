"""Experiment ledger: hypothesis → change → outcome → decision, one entry per experiment.

The team's memory of what was tried and what it did. An entry names a baseline and one or more candidates (runs or
run groups); the **outcome** is filled in automatically and deterministically — the meaningful config changes, the
metrics that moved (with the significance basis), and the training health problems of each candidate — while the
**hypothesis** and the **decision** are written by people. A decided entry can be promoted to knowledge (as a
*proposed* entry: a belief with a test when the change is one numeric config key, else a fact).

Entries live in the local document store (never in Azure). Ids: ``L-<yyyymmdd>-<n>``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from model_forensic.shared.errors import NotFoundError, ValidationError

KIND = "ledger"
STATUSES = ("planned", "running", "analysed", "decided")
DECISIONS = ("", "adopt", "reject", "inconclusive", "follow-up")
EDITABLE = (
    "title",
    "hypothesis",
    "change",
    "baseline",
    "candidates",
    "decision",
    "decision_note",
    "notes",
    "tags",
    "status",
    "study",
)
MAX_TEXT = 20_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.4g}"


def outcome(svc: Any, baseline: str, candidate: str, top: int = 8) -> dict[str, Any]:
    """The automatic part of an entry for one candidate: config changes, moved metrics, health problems."""
    from model_forensic.analysis.loss_questions import ask

    cmp = svc.compare(baseline, candidate)
    meaningful = list(cmp.config_diff.meaningful)
    cats: dict[str, int] = {}
    for c in meaningful:
        cats[c.category] = cats.get(c.category, 0) + 1
    moved = sorted(
        (d for d in cmp.deltas if d.verdict.value in ("improved", "regressed")),
        key=lambda d: -abs(d.relative if d.relative is not None else 0.0),
    )
    problems: list[dict[str, str]] = []
    try:
        cur = {str(s.key): s for s in svc.series(candidate)}
        base = {str(s.key): s for s in svc.series(baseline)}
        problems = [
            {"id": a.id, "status": a.status, "question": a.question, "answer": a.answer}
            for a in ask(cur, base)
            if a.status in ("bad", "warn")
        ]
    except Exception as e:  # the outcome must not fail on a series problem
        problems = [{"id": "error", "status": "na", "question": "health questions", "answer": str(e)}]
    return {
        "candidate": candidate,
        "computed_at": _now(),
        "config_changes": [
            {
                "path": c.path,
                "category": c.category,
                "baseline": c.baseline,
                "candidate": c.candidate,
                "text": c.describe(),
            }
            for c in meaningful[:40]
        ],
        "config_categories": cats,
        "improved": sum(1 for d in cmp.deltas if d.verdict.value == "improved"),
        "regressed": sum(1 for d in cmp.deltas if d.verdict.value == "regressed"),
        "significance": "replicates" if cmp.baseline_replicates else "threshold",
        "moved": [
            {
                "metric": d.metric,
                "baseline": d.baseline,
                "candidate": d.candidate,
                "relative": d.relative,
                "verdict": d.verdict.value,
                "z": d.z_score,
            }
            for d in moved[:top]
        ],
        "problems": problems[:8],
    }


@dataclass
class LedgerService:
    docs: Any  # DocumentStore
    svc: Any  # ForensicService

    # ── storage ────────────────────────────────────────────────────────────────────────────────────────────────
    def _new_id(self) -> str:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        n = self.docs.list_summaries(KIND, text=f"L-{day}-", limit=1)[1]
        while True:
            n += 1
            cand = f"L-{day}-{n}"
            try:
                self.docs.get_raw(KIND, cand)
            except NotFoundError:
                return cand

    def _save(self, e: dict[str, Any]) -> dict[str, Any]:
        e["updated_at"] = _now()
        if len(json.dumps(e, default=str)) > 512 * 1024:
            raise ValidationError("ledger entry larger than 512 KiB")
        summary = {
            k: e.get(k)
            for k in (
                "id",
                "title",
                "status",
                "decision",
                "baseline",
                "candidates",
                "tags",
                "owner",
                "created_at",
                "updated_at",
            )
        }
        summary["headline"] = self._headline(e)
        self.docs.put(KIND, e["id"], e, summary=summary)
        return e

    @staticmethod
    def _headline(e: dict[str, Any]) -> str:
        """One line for lists: the biggest mover of the first candidate, or the problem count."""
        for o in (e.get("outcome") or {}).values():
            if o.get("moved"):
                m = o["moved"][0]
                rel = f"{m['relative']:+.1%}" if m.get("relative") is not None else ""
                return f"{m['metric']} {rel} · {o.get('improved', 0)}↑ {o.get('regressed', 0)}↓"
            return f"{o.get('improved', 0)}↑ {o.get('regressed', 0)}↓ · {len(o.get('problems', []))} problems"
        return ""

    @staticmethod
    def _check(fields: dict[str, Any]) -> None:
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValidationError(f"status must be one of {', '.join(STATUSES)}")
        if "decision" in fields and fields["decision"] not in DECISIONS:
            raise ValidationError("decision must be one of: (empty), " + ", ".join(d for d in DECISIONS if d))
        for k in ("title", "hypothesis", "change", "decision_note", "notes"):
            if k in fields and len(str(fields[k] or "")) > MAX_TEXT:
                raise ValidationError(f"{k} longer than {MAX_TEXT} characters")
        if "candidates" in fields and not isinstance(fields["candidates"], list):
            raise ValidationError("candidates must be a list of run ids")
        if "tags" in fields and not isinstance(fields["tags"], list):
            raise ValidationError("tags must be a list")

    # ── API ────────────────────────────────────────────────────────────────────────────────────────────────────
    def create(self, owner: str, fields: dict[str, Any], analyse: bool = True) -> dict[str, Any]:
        unknown = set(fields) - set(EDITABLE)
        if unknown:
            raise ValidationError(f"unknown fields: {', '.join(sorted(unknown))}")
        self._check(fields)
        e: dict[str, Any] = {
            "id": self._new_id(),
            "owner": owner,
            "created_at": _now(),
            "title": (fields.get("title") or "").strip()[:200] or "Untitled experiment",
            "hypothesis": fields.get("hypothesis", ""),
            "change": fields.get("change", ""),
            "baseline": fields.get("baseline") or None,
            "candidates": [c for c in fields.get("candidates", []) if c],
            "status": fields.get("status") or "planned",
            "decision": fields.get("decision", ""),
            "decision_note": fields.get("decision_note", ""),
            "notes": fields.get("notes", ""),
            "tags": fields.get("tags", []),
            "study": fields.get("study"),
            "outcome": {},
            "history": [{"at": _now(), "by": owner, "what": "created"}],
        }
        if analyse and e["baseline"] and e["candidates"]:
            self._analyse(e)
        return self._save(e)

    def get(self, entry_id: str) -> dict[str, Any]:
        e: dict[str, Any] = self.docs.get_raw(KIND, entry_id)
        return e

    def find(
        self, status: str | None = None, text: str | None = None, limit: int = 100
    ) -> tuple[list[dict[str, Any]], int]:
        items, total = self.docs.list_summaries(
            KIND, where={"status": status} if status else None, text=text or None, limit=limit
        )
        return items, total

    def update(self, entry_id: str, by: str, fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - set(EDITABLE)
        if unknown:
            raise ValidationError(f"unknown fields: {', '.join(sorted(unknown))}")
        self._check(fields)
        e = self.get(entry_id)
        changed = [k for k, v in fields.items() if e.get(k) != v]
        e.update(fields)
        if fields.get("decision") and "status" not in fields:
            e["status"] = "decided"
        if changed:
            e.setdefault("history", []).append({"at": _now(), "by": by, "what": "changed " + ", ".join(changed)})
        if {"baseline", "candidates"} & set(changed) and e.get("baseline") and e.get("candidates"):
            self._analyse(e)
        return self._save(e)

    def refresh(self, entry_id: str, by: str = "system") -> dict[str, Any]:
        e = self.get(entry_id)
        if not (e.get("baseline") and e.get("candidates")):
            raise ValidationError("the entry needs a baseline and at least one candidate")
        self._analyse(e)
        e.setdefault("history", []).append({"at": _now(), "by": by, "what": "outcome refreshed"})
        return self._save(e)

    def delete(self, entry_id: str) -> None:
        self.get(entry_id)
        self.docs.delete(KIND, entry_id)

    def _analyse(self, e: dict[str, Any]) -> None:
        out: dict[str, Any] = {}
        for c in e["candidates"]:
            try:
                out[c] = outcome(self.svc, e["baseline"], c)
            except Exception as exc:
                out[c] = {"candidate": c, "error": f"{type(exc).__name__}: {exc}", "computed_at": _now()}
        e["outcome"] = out
        if not e.get("change"):
            cats: dict[str, int] = next(
                (o.get("config_categories") for o in out.values() if o.get("config_categories")), {}
            )
            e["change_auto"] = ", ".join(f"{k} ({v})" for k, v in sorted(cats.items(), key=lambda kv: -kv[1]))
        if e.get("status") in ("planned", "running"):
            e["status"] = "analysed"

    # ── export / knowledge ─────────────────────────────────────────────────────────────────────────────────────
    def markdown(self, entry_ids: list[str] | None = None) -> str:
        ids = entry_ids or [s["id"] for s in self.find(limit=1000)[0]]
        parts = ["# Experiment ledger", ""]
        for i in ids:
            e = self.get(i)
            parts += [
                f"## {e['id']} — {e['title']}",
                "",
                f"*{e['status']}*"
                + (f" · decision: **{e['decision']}**" if e.get("decision") else "")
                + f" · baseline `{e.get('baseline') or '—'}` · candidates "
                + ", ".join(f"`{c}`" for c in e.get("candidates", [])),
                "",
                f"**Hypothesis.** {e.get('hypothesis') or '—'}",
                "",
                f"**Change.** {e.get('change') or e.get('change_auto') or '—'}",
                "",
            ]
            for c, o in (e.get("outcome") or {}).items():
                if o.get("error"):
                    parts += [f"**Outcome `{c}`.** could not compute: {o['error']}", ""]
                    continue
                parts.append(
                    f"**Outcome `{c}`** ({o['improved']} improved, {o['regressed']} regressed; "
                    f"significance: {o['significance']})"
                )
                parts += [
                    f"- {m['metric']}: {_fmt(m['baseline'])} → {_fmt(m['candidate'])}"
                    + (f" ({m['relative']:+.1%})" if m.get("relative") is not None else "")
                    + f", {m['verdict']}"
                    for m in o.get("moved", [])
                ]
                parts += [f"- config: {ch['text']}" for ch in o.get("config_changes", [])[:10]]
                parts += [f"- health ({p['status']}): {p['question']} {p['answer']}" for p in o.get("problems", [])]
                parts.append("")
            if e.get("decision_note"):
                parts += [f"**Decision.** {e['decision_note']}", ""]
            if e.get("notes"):
                parts += [f"**Notes.** {e['notes']}", ""]
        return "\n".join(parts).rstrip() + "\n"

    def knowledge_yaml(self, entry_id: str) -> str:
        """A proposed knowledge entry from a decided ledger entry (YAML for ``KnowledgeStore.import_text``)."""
        import yaml

        e = self.get(entry_id)
        if not e.get("decision"):
            raise ValidationError("decide the entry first (adopt / reject / inconclusive / follow-up)")
        o = next((v for v in (e.get("outcome") or {}).values() if not v.get("error")), None)
        moved = (o or {}).get("moved", [])
        changes = (o or {}).get("config_changes", [])
        statement = f"{e['title']}: {e['decision']}" + (f" — {e['decision_note']}" if e.get("decision_note") else "")
        entry: dict[str, Any] = {
            "id": "K-ledger-" + re.sub(r"[^a-z0-9]+", "-", e["id"].lower()).strip("-"),
            "kind": "fact",
            "statement": statement[:500],
            "why": (e.get("hypothesis") or "")[:1000],
            "source": {
                "by": e.get("owner", ""),
                "evidence": f"ledger {e['id']}; baseline {e.get('baseline')}; "
                f"candidates {', '.join(e.get('candidates', []))}",
            },
            "confidence": "medium" if (o or {}).get("significance") == "replicates" else "low",
        }
        num = [
            c
            for c in changes
            if isinstance(c.get("baseline"), (int, float)) and isinstance(c.get("candidate"), (int, float))
        ]
        if len(changes) == 1 and num and moved:
            c, m = num[0], moved[0]
            up = (m.get("relative") or 0) > 0
            entry["kind"] = "belief"
            entry["test"] = {
                "type": "config_effect",
                "key": c["path"],
                "change": "increase" if c["candidate"] > c["baseline"] else "decrease",
                "metric": m["metric"],
                "expect": "up" if up else "down",
            }
        return str(yaml.safe_dump({"entries": [entry]}, sort_keys=False, allow_unicode=True))
