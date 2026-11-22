"""Study use cases: build up the record of one investigation, op by op.

Every change is a small server-side operation applied to the latest stored study under a lock, so two
browser tabs (or the UI and the CLI) never overwrite each other's edits. Each operation appends a
timeline event.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from model_forensic.application.ports import StudyStore
from model_forensic.application.service import ForensicService
from model_forensic.domain.comparison import Comparison, DeltaVerdict
from model_forensic.domain.run import RunRef
from model_forensic.domain.study import (
    Decision,
    DecisionOutcome,
    EvidenceLink,
    Gate,
    GateCheck,
    HypothesisStatus,
    Preregistration,
    Study,
    StudyEvent,
    StudyHypothesis,
    StudyNote,
)
from model_forensic.shared import identity
from model_forensic.shared.errors import NotFoundError, ValidationError

MAX_RUNS = 12


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StudyService:
    def __init__(self, store: StudyStore, svc: ForensicService) -> None:
        self.store = store
        self.svc = svc
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _apply(self, study_id: str, fn: Callable[[Study], Study], event: tuple[str, str] | None) -> Study:
        with self._lock:
            s = fn(self.store.get(study_id))
            if event:
                ev = StudyEvent(kind=event[0], text=event[1], actor=identity.current().id)
                s = s.model_copy(update={"timeline": (*s.timeline, ev)[-500:]})
            s = s.model_copy(update={"updated_at": _now()})
            self.store.save(s)
            return s

    def _ref(self, run: str | RunRef) -> RunRef:
        ref = self.svc.ref(run)
        self.svc.get_run(ref)  # validates existence (NotFoundError otherwise)
        return ref

    @staticmethod
    def _replace_h(s: Study, h: StudyHypothesis) -> Study:
        return s.model_copy(update={"hypotheses": tuple(h if x.id == h.id else x for x in s.hypotheses)})

    # ------------------------------------------------------------------ studies
    def create(self, title: str, goal: str = "", runs: list[str] | None = None, baseline: str | None = None) -> Study:
        if not title.strip():
            raise ValidationError("a study needs a title")
        refs = tuple(dict.fromkeys(self._ref(r) for r in runs or []))[:MAX_RUNS]
        base = self._ref(baseline) if baseline else None
        if base and base not in refs:
            refs = (base, *refs)
        s = Study(
            id=_id("st"),
            owner=identity.current().id,
            title=title.strip(),
            goal=goal.strip(),
            runs=refs,
            baseline=base,
            timeline=(StudyEvent(kind="created", text=f"Study created with {len(refs)} run(s)"),),
        )
        self.store.save(s)
        return s

    def get(self, study_id: str) -> Study:
        return self.store.get(study_id)

    def all(self) -> list[Study]:
        return sorted(self.store.list(), key=lambda s: s.updated_at, reverse=True)

    def delete(self, study_id: str) -> None:
        self.store.get(study_id)
        self.store.delete(study_id)

    def set_status(self, study_id: str, status: str) -> Study:
        if status not in ("open", "closed"):
            raise ValidationError("status must be 'open' or 'closed'")
        return self._apply(
            study_id,
            lambda s: s.model_copy(update={"status": status}),
            ("closed" if status == "closed" else "reopened", f"Study {status if status == 'closed' else 'reopened'}"),
        )

    def update(self, study_id: str, title: str | None = None, goal: str | None = None) -> Study:
        patch: dict[str, Any] = {}
        if title is not None:
            if not title.strip():
                raise ValidationError("title cannot be empty")
            patch["title"] = title.strip()
        if goal is not None:
            patch["goal"] = goal.strip()
        return self._apply(
            study_id, lambda s: s.model_copy(update=patch), ("edited", "Title/goal updated") if patch else None
        )

    def add_run(self, study_id: str, run: str) -> Study:
        ref = self._ref(run)

        def fn(s: Study) -> Study:
            if ref in s.runs:
                return s
            if len(s.runs) >= MAX_RUNS:
                raise ValidationError(f"a study holds at most {MAX_RUNS} runs; start a new study for more")
            return s.model_copy(update={"runs": (*s.runs, ref)})

        return self._apply(study_id, fn, ("run_added", f"Added run {ref}"))

    def remove_run(self, study_id: str, run: str) -> Study:
        ref = self.svc.ref(run)
        return self._apply(
            study_id,
            lambda s: s.model_copy(
                update={
                    "runs": tuple(r for r in s.runs if r != ref),
                    "baseline": None if s.baseline == ref else s.baseline,
                }
            ),
            ("run_removed", f"Removed run {ref}"),
        )

    def set_baseline(self, study_id: str, run: str | None) -> Study:
        ref = self._ref(run) if run else None

        def fn(s: Study) -> Study:
            runs = s.runs if ref is None or ref in s.runs else (ref, *s.runs)
            return s.model_copy(update={"baseline": ref, "runs": runs})

        return self._apply(study_id, fn, ("baseline", f"Baseline set to {ref}" if ref else "Baseline cleared"))

    # ------------------------------------------------------------------ notes, pins, investigations
    def add_note(self, study_id: str, text: str) -> Study:
        if not text.strip():
            raise ValidationError("empty note")
        note = StudyNote(id=_id("nt"), text=text.strip(), author=identity.current().id)
        return self._apply(
            study_id, lambda s: s.model_copy(update={"notes": (*s.notes, note)}), ("note", text.strip()[:120])
        )

    def delete_note(self, study_id: str, note_id: str) -> Study:
        return self._apply(
            study_id, lambda s: s.model_copy(update={"notes": tuple(n for n in s.notes if n.id != note_id)}), None
        )

    def pin_finding(self, study_id: str, finding_id: str, pinned: bool = True) -> Study:
        f = self.svc.findings.get(finding_id) if pinned else None

        def fn(s: Study) -> Study:
            ids = tuple(x for x in s.pinned_findings if x != finding_id)
            return s.model_copy(update={"pinned_findings": (*ids, finding_id) if pinned else ids})

        return self._apply(study_id, fn, ("pinned", f"Pinned finding: {f.title}") if f else None)

    def attach_investigation(self, study_id: str, investigation_id: str, question: str = "") -> Study:
        def fn(s: Study) -> Study:
            if investigation_id in s.investigations:
                return s
            return s.model_copy(update={"investigations": (*s.investigations, investigation_id)})

        return self._apply(
            study_id, fn, ("ai", f"AI investigation: {question[:120]}" if question else "AI investigation attached")
        )

    # ------------------------------------------------------------------ hypotheses
    def add_hypothesis(
        self,
        study_id: str,
        statement: str,
        rationale: str = "",
        proposed_change: str = "",
        evidence: list[EvidenceLink] | None = None,
        source: str = "manual",
        reference_run: str | None = None,
    ) -> Study:
        if not statement.strip():
            raise ValidationError("empty hypothesis")
        h = StudyHypothesis(
            id=_id("hy"),
            statement=statement.strip(),
            rationale=rationale.strip(),
            proposed_change=proposed_change.strip(),
            evidence=tuple(evidence or ()),
            source=source,
            reference_run=self._ref(reference_run) if reference_run else None,
        )
        return self._apply(
            study_id,
            lambda s: s.model_copy(update={"hypotheses": (*s.hypotheses, h)}),
            ("hypothesis", f"New hypothesis: {h.statement[:120]}"),
        )

    def update_hypothesis(
        self,
        study_id: str,
        hid: str,
        *,
        statement: str | None = None,
        status: HypothesisStatus | None = None,
        rationale: str | None = None,
        proposed_change: str | None = None,
        verdict: str | None = None,
        test_run: str | None = None,
        reference_run: str | None = None,
        clear_test_run: bool = False,
    ) -> Study:
        test_ref = self._ref(test_run) if test_run else None
        ref_ref = self._ref(reference_run) if reference_run else None
        events: list[str] = []

        def fn(s: Study) -> Study:
            try:
                h = s.hypothesis(hid)
            except KeyError:
                raise NotFoundError(f"hypothesis {hid} not in study") from None
            patch: dict[str, Any] = {"updated_at": _now()}
            for k, v in (
                ("statement", statement),
                ("rationale", rationale),
                ("proposed_change", proposed_change),
                ("verdict", verdict),
            ):
                if v is not None:
                    patch[k] = v.strip()
            if status is not None and status != h.status:
                patch["status"] = status
                events.append(f"Hypothesis “{h.statement[:80]}” → {status.value}")
            if test_ref is not None:
                patch["test_run"] = test_ref
                events.append(f"Test run {test_ref} linked to “{h.statement[:80]}”")
            elif clear_test_run:
                patch["test_run"] = None
            if ref_ref is not None:
                patch["reference_run"] = ref_ref
            s2 = self._replace_h(s, h.model_copy(update=patch))
            if test_ref is not None and test_ref not in s2.runs and len(s2.runs) < MAX_RUNS:
                s2 = s2.model_copy(update={"runs": (*s2.runs, test_ref)})
            return s2

        s = self._apply(study_id, fn, None)
        for e in events:
            s = self._apply(study_id, lambda x: x, ("hypothesis", e))
        return s

    def add_evidence(self, study_id: str, hid: str, link: EvidenceLink) -> Study:
        def fn(s: Study) -> Study:
            h = s.hypothesis(hid)
            if any(e.kind == link.kind and e.ref == link.ref for e in h.evidence):
                return s
            return self._replace_h(s, h.model_copy(update={"evidence": (*h.evidence, link), "updated_at": _now()}))

        return self._apply(study_id, fn, None)

    def remove_evidence(self, study_id: str, hid: str, kind: str, ref: str) -> Study:
        def fn(s: Study) -> Study:
            h = s.hypothesis(hid)
            return self._replace_h(
                s,
                h.model_copy(
                    update={"evidence": tuple(e for e in h.evidence if not (e.kind.value == kind and e.ref == ref))}
                ),
            )

        return self._apply(study_id, fn, None)

    def delete_hypothesis(self, study_id: str, hid: str) -> Study:
        return self._apply(
            study_id,
            lambda s: s.model_copy(update={"hypotheses": tuple(h for h in s.hypotheses if h.id != hid)}),
            ("hypothesis", "Hypothesis deleted"),
        )

    def verify(self, study_id: str, hid: str) -> tuple[StudyHypothesis, Comparison, str]:
        """Compare the hypothesis' test run against its reference run. Returns a suggested verdict (not applied)."""
        s = self.store.get(study_id)
        try:
            h = s.hypothesis(hid)
        except KeyError:
            raise NotFoundError(f"hypothesis {hid} not in study") from None
        if h.test_run is None:
            raise ValidationError("link a test run to this hypothesis first")
        ref = h.reference_run or s.baseline or next((r for r in s.runs if r != h.test_run), None)
        if ref is None:
            raise ValidationError("no reference run: set a study baseline or the hypothesis' reference run")
        cmp = self.svc.compare(ref, h.test_run)
        imp = [d for d in cmp.deltas if d.verdict == DeltaVerdict.IMPROVED]
        reg = [d for d in cmp.deltas if d.verdict == DeltaVerdict.REGRESSED]
        basis = (
            "seed replicates" if cmp.baseline_replicates else "fixed thresholds (no replicates — treat as tentative)"
        )
        changed = len(cmp.config_diff.meaningful)
        suggestion = (
            f"{h.test_run.run_id} vs {ref.run_id}: {len(imp)} metric(s) improved, {len(reg)} regressed beyond noise "
            f"(significance: {basis}); {changed} config change(s)"
            + (
                " — more than one change, so the effect cannot be attributed to this hypothesis alone."
                if changed > 1
                else "."
            )
        )
        st = self.store.get(study_id)
        if st.gate is not None and st.gate.blocking:
            suggestion += (
                " Caution: the study's checks block a verdict ("
                + ", ".join(st.gate.blocking)
                + ") — the numbers may not measure what they claim."
            )
        return h, cmp, suggestion

    # ------------------------------------------------------------------ export
    # ------------------------------------------------------------------ wizard: pre-registration, gate, decision
    def create_from_template(
        self,
        template: str,
        title: str,
        runs: list[str],
        baseline: str | None = None,
        claim: str = "",
        deciding_metric: str = "",
        falsified_if: str = "",
        confounders: list[str] | None = None,
        goal: str = "",
    ) -> Study:
        from model_forensic.analysis.study_checks import config_values, ruler_identity
        from model_forensic.application.study_templates import BY_ID

        t = BY_ID.get(template)
        if t is None:
            raise ValidationError(f"unknown template {template!r}", details={"available": sorted(BY_ID)})
        if not runs:
            raise ValidationError("pick at least one run")
        if t.needs_baseline and not baseline:
            raise ValidationError(f"'{t.title}' needs a baseline run")
        s = self.create(title or t.title, goal or t.question, runs, baseline)
        cand = str(s.runs[-1]) if s.runs else ""
        fill = {"candidate": cand, "baseline": str(s.baseline or ""), "metric": deciding_metric or t.default_metric}
        ruler = None
        if s.baseline is not None:
            vals = [
                v
                for v in config_values(self.svc.flat_config(s.baseline), "combined_metrics_yaml").values()
                if isinstance(v, str)
            ]
            ruler = ruler_identity(vals[0]) if vals else None
        pre = Preregistration(
            template=t.id,
            question=t.question.format(**fill),
            claim=(claim or t.claim).format(**fill),
            deciding_metric=fill["metric"],
            ruler=ruler,
            falsified_if=(falsified_if or t.falsified_if).format(**fill),
            confounders=tuple(confounders if confounders is not None else t.confounders),
            analyses=t.analyses,
        )
        goal_text = (goal or t.question).format(**fill)
        s = self._apply(
            s.id,
            lambda x: x.model_copy(update={"preregistration": pre, "goal": goal_text}),
            ("preregistered", f"Pre-registered: {pre.claim[:120]}"),
        )
        return self.add_hypothesis(
            s.id,
            pre.claim,
            rationale=f"Decided by {pre.deciding_metric}. Falsified if: {pre.falsified_if}",
            source="preregistered",
        )

    def check_confounder(self, study_id: str, confounder: str, checked: bool = True) -> Study:
        def fn(s: Study) -> Study:
            if s.preregistration is None or confounder not in s.preregistration.confounders:
                raise ValidationError("unknown confounder")
            done = [c for c in s.preregistration.confounders_checked if c != confounder] + (
                [confounder] if checked else []
            )
            return s.model_copy(
                update={"preregistration": s.preregistration.model_copy(update={"confounders_checked": tuple(done)})}
            )

        return self._apply(study_id, fn, ("confounder", f"{'Checked' if checked else 'Unchecked'}: {confounder}"))

    def run_gate(self, study_id: str) -> Study:
        """Pitfall checks P1–P8 over every run; failing measurement checks block a verdict."""
        from model_forensic.analysis import study_checks as sc
        from model_forensic.analysis.base import AnalysisContext
        from model_forensic.analysis.plugins.study import run_checks
        from model_forensic.application.study_templates import MEASUREMENT_CHECKS

        s = self.store.get(study_id)
        runs = list(dict.fromkeys([*([s.baseline] if s.baseline else []), *s.runs]))
        checks: list[GateCheck] = []
        configs = {}
        for r in runs:
            rd = self.svc._safe_run_data(r, with_evals=True, with_config=True, with_lr=True)
            configs[str(r)] = dict(rd.configs)
            for c in run_checks(AnalysisContext(target=rd, registry=self.svc.metrics)):
                if c.id != "P1":
                    checks.append(GateCheck(id=c.id, title=c.title, status=c.status, run=str(r), detail=c.detail))
        p1 = sc.check_ruler(configs)
        checks.insert(0, GateCheck(id="P1", title=p1.title, status=p1.status, run="all runs", detail=p1.detail))
        blocking = tuple(f"{c.id} on {c.run}" for c in checks if c.status == "FAIL" and c.id in MEASUREMENT_CHECKS)
        gate = Gate(checks=tuple(checks), blocking=blocking)
        msg = f"Checks run: {len(blocking)} blocking" + (f" ({', '.join(blocking)})" if blocking else " — gate open")
        return self._apply(study_id, lambda x: x.model_copy(update={"gate": gate}), ("gate", msg))

    def decide(
        self,
        study_id: str,
        outcome: str,
        decision: str,
        rationale: str = "",
        based_on: list[str] | None = None,
        gate_override: str = "",
    ) -> Study:
        if not decision.strip():
            raise ValidationError("write down what was decided")
        if outcome not in {o.value for o in DecisionOutcome}:
            raise ValidationError(f"outcome must be one of {', '.join(o.value for o in DecisionOutcome)}")
        s = self.store.get(study_id)
        if s.gate is None:
            s = self.run_gate(study_id)
        assert s.gate is not None
        if s.gate.blocking and outcome in {"adopt", "reject"} and not gate_override.strip():
            raise ValidationError(
                "the checks invalidate a verdict: "
                + ", ".join(s.gate.blocking)
                + " — fix them, choose 'more evidence' / 'inconclusive', or give an override reason",
                details={"blocking": list(s.gate.blocking)},
            )
        d = Decision(
            outcome=DecisionOutcome(outcome),
            decision=decision.strip(),
            rationale=rationale.strip(),
            based_on=tuple(based_on or ()),
            gate_override=gate_override.strip(),
            decided_by=identity.current().id,
        )
        return self._apply(
            study_id,
            lambda x: x.model_copy(update={"decision": d}),
            ("decision", f"Decision ({d.outcome.value}): {d.decision[:120]}"),
        )

    def to_markdown(self, study_id: str) -> str:
        s = self.store.get(study_id)
        out = [f"# Study: {s.title}", ""]
        if s.goal:
            out += [f"**Goal:** {s.goal}", ""]
        out += [f"_Created {s.created_at:%Y-%m-%d}, updated {s.updated_at:%Y-%m-%d %H:%M} UTC._", ""]
        if s.decision:
            d = s.decision
            out += ["## Decision", "", f"**{d.outcome.value.replace('_', ' ').upper()}** — {d.decision}", ""]
            if d.rationale:
                out += [d.rationale, ""]
            if d.based_on:
                out += ["Based on: " + ", ".join(f"`{b}`" for b in d.based_on), ""]
            if d.gate_override:
                out += [f"> Gate overridden: {d.gate_override}", ""]
            out += [f"_Decided {d.decided_at:%Y-%m-%d %H:%M} UTC._", ""]
        if s.preregistration:
            pr = s.preregistration
            out += [
                "## Pre-registration",
                "",
                f"- **Question:** {pr.question}",
                f"- **Claim:** {pr.claim}",
                f"- **Decided by:** `{pr.deciding_metric}`" + (f" (ruler `{pr.ruler}`)" if pr.ruler else ""),
                f"- **Falsified if:** {pr.falsified_if}",
                "- **Confounders checked first:**",
            ]
            out += [f"  - [{'x' if c in pr.confounders_checked else ' '}] {c}" for c in pr.confounders]
            out += [f"_Registered {pr.registered_at:%Y-%m-%d %H:%M} UTC._", ""]
        if s.gate:
            out += ["## Checks (P1–P8)", "", "| Check | Status | Run | Detail |", "|---|---|---|---|"]
            out += [
                f"| {c.id} {c.title} | {c.status} | {c.run} | {c.detail.replace('|', '/')} |" for c in s.gate.checks
            ]
            out += ["", f"**Gate:** {'open' if s.gate.open else 'blocked by ' + ', '.join(s.gate.blocking)}", ""]
        out += ["## Runs", ""]
        for r in s.runs:
            run = self.svc.get_run(r)
            role = "baseline" if r == s.baseline else "run"
            out.append(
                f"- `{r}` ({role}) — {run.status.value}, {run.user or '?'}, commit `{(run.git.commit or '—')[:8]}`"
                + (f": {run.description}" if run.description else "")
            )
        order = [
            HypothesisStatus.SUPPORTED,
            HypothesisStatus.OPEN,
            HypothesisStatus.INCONCLUSIVE,
            HypothesisStatus.REJECTED,
        ]
        out += ["", "## Hypotheses", ""]
        if not s.hypotheses:
            out.append("_None yet._")
        for st in order:
            for h in [h for h in s.hypotheses if h.status == st]:
                out.append(f"### [{h.status.value.upper()}] {h.statement}")
                if h.rationale:
                    out.append(f"\n{h.rationale}")
                if h.evidence:
                    out.append("\nEvidence:")
                    out += [
                        f"- {'for' if e.supports else 'against'} · {e.kind.value}: {e.label}"
                        + (f" (`{e.run}`)" if e.run else "")
                        for e in h.evidence
                    ]
                if h.proposed_change:
                    out.append(f"\n**Proposed change:** {h.proposed_change}")
                if h.test_run:
                    out.append(
                        f"\n**Tested by:** `{h.test_run}`" + (f" vs `{h.reference_run}`" if h.reference_run else "")
                    )
                if h.verdict:
                    out.append(f"\n**Verdict:** {h.verdict}")
                out.append("")
        if s.pinned_findings:
            out += ["## Pinned findings", ""]
            for fid in s.pinned_findings:
                try:
                    f = self.svc.findings.get(fid)
                    out.append(f"- **{f.title}** ({f.severity.value}, {f.label.value}) — {f.statement}")
                except NotFoundError:
                    out.append(f"- `{fid}` (no longer stored)")
            out.append("")
        if s.notes:
            out += ["## Notes", ""] + [f"- {n.created_at:%Y-%m-%d %H:%M} — {n.text}" for n in s.notes] + [""]
        out += ["## Timeline", ""] + [f"- {e.at:%Y-%m-%d %H:%M} · {e.text}" for e in s.timeline]
        return "\n".join(out) + "\n"
