import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Gavel, Loader2, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import type { DecisionOutcome, Study } from "../../api/types";
import { useApp } from "../../components/AppState";
import { Card, ErrorBox, Pill } from "../../components/ui";
import { fmtAgo } from "../../lib/format";

const OUTCOMES: { id: DecisionOutcome; label: string; verdict: boolean }[] = [
  { id: "adopt", label: "Adopt the change", verdict: true },
  { id: "reject", label: "Reject the change", verdict: true },
  { id: "more_evidence", label: "Need more evidence", verdict: false },
  { id: "inconclusive", label: "Inconclusive", verdict: false },
];
const TONE: Record<string, string> = { FAIL: "bad", WARN: "warn", PASS: "good", INFO: "outline", SKIP: "outline" };

/** Pre-registration → checks first → decision record. */
export function DecideTab({ study, onChange }: { study: Study; onChange: (s: Study) => void }) {
  const { toast } = useApp();
  const pr = study.preregistration;
  const gate = study.gate;
  const [outcome, setOutcome] = useState<DecisionOutcome>(study.decision?.outcome ?? "more_evidence");
  const [decision, setDecision] = useState(study.decision?.decision ?? "");
  const [rationale, setRationale] = useState(study.decision?.rationale ?? "");
  const [override, setOverride] = useState("");
  const runGate = useMutation({ mutationFn: () => api.studyGate(study.id), onSuccess: (s) => { onChange(s); toast(s.gate?.blocking.length ? "Checks block a verdict" : "Gate open", s.gate?.blocking.length ? "bad" : "good"); } });
  const toggle = useMutation({ mutationFn: (v: { c: string; on: boolean }) => api.studyConfounder(study.id, v.c, v.on), onSuccess: onChange });
  const decide = useMutation({
    mutationFn: () => api.studyDecide(study.id, { outcome, decision, rationale, gate_override: override || undefined, based_on: study.hypotheses.filter((h) => h.status === "supported").map((h) => h.id) }),
    onSuccess: (s) => { onChange(s); toast("Decision recorded", "good"); },
  });
  const blocked = !!gate && gate.blocking.length > 0;
  const needsOverride = blocked && OUTCOMES.find((o) => o.id === outcome)?.verdict;
  return (
    <div className="col" style={{ gap: 14 }}>
      {pr ? (
        <Card title="Pre-registration" sub={`registered ${fmtAgo(pr.registered_at)} — written before looking at results`}>
          <div className="col" style={{ gap: 8 }}>
            <div><span className="faint">Question</span><div>{pr.question}</div></div>
            <div><span className="faint">Claim</span><div><b>{pr.claim}</b></div></div>
            <div className="row wrap" style={{ gap: 16 }}>
              <div><span className="faint">Decided by</span><div className="mono">{pr.deciding_metric}</div></div>
              {pr.ruler && <div><span className="faint">Ruler</span><div className="mono">{pr.ruler}</div></div>}
            </div>
            <div><span className="faint">Falsified if</span><div>{pr.falsified_if}</div></div>
            <div>
              <span className="faint">Confounders to rule out first ({pr.confounders_checked.length}/{pr.confounders.length})</span>
              {pr.confounders.map((c) => (
                <label key={c} className="row" style={{ gap: 8, margin: "4px 0" }}>
                  <input type="checkbox" checked={pr.confounders_checked.includes(c)} onChange={(e) => toggle.mutate({ c, on: e.target.checked })} /> {c}
                </label>
              ))}
            </div>
          </div>
        </Card>
      ) : (
        <div className="note">This study was created without the wizard — no pre-registration. Use <b>Guided study</b> on the Studies page next time.</div>
      )}
      <Card title={<span className="row">{blocked ? <ShieldAlert size={15} color="var(--bad)" /> : <ShieldCheck size={15} color="var(--good)" />} Checks first (P1–P8)</span>}
        right={<button className="btn sm" disabled={runGate.isPending} onClick={() => runGate.mutate()}>{runGate.isPending ? <Loader2 size={13} className="spin" /> : null} {gate ? "Re-run checks" : "Run checks"}</button>}>
        {!gate && <div className="muted">Run the checks before deciding: a failing measurement check (different rulers, degenerate ground truth, …) means the numbers do not measure what they claim.</div>}
        {gate && (
          <>
            <div className={blocked ? "error-box" : "note"} style={{ marginBottom: 10 }}>
              {blocked ? <ShieldAlert size={16} color="var(--bad)" /> : <CheckCircle2 size={16} color="var(--good)" />}
              <div>
                {blocked ? <>Verdicts are blocked by <b>{gate.blocking.join(", ")}</b>. Fix them, decide “more evidence / inconclusive”, or give an override reason.</> : <>Gate open — no measurement check fails.</>}
                <span className="faint" style={{ marginLeft: 8 }}>checked {fmtAgo(gate.checked_at)}</span>
              </div>
            </div>
            <div className="table-wrap" style={{ maxHeight: 320 }}>
              <table className="t"><thead><tr><th>#</th><th>Status</th><th>Run</th><th>Detail</th></tr></thead>
                <tbody>{gate.checks.filter((c) => c.status !== "SKIP").map((c, i) => (
                  <tr key={i}><td className="mono">{c.id}</td><td><Pill tone={TONE[c.status] ?? "outline"}>{c.status}</Pill></td><td className="mono faint">{c.run}</td><td className="wrap" style={{ fontSize: 12.5 }}>{c.detail}</td></tr>
                ))}</tbody></table>
            </div>
          </>
        )}
        {runGate.error && <ErrorBox error={runGate.error} />}
      </Card>
      <Card title={<span className="row"><Gavel size={15} /> Decision record</span>} sub={study.decision ? `recorded ${fmtAgo(study.decision.decided_at)}` : "what we decided, based on what"}>
        <div className="col" style={{ gap: 10 }}>
          <div className="row wrap" style={{ gap: 6 }}>{OUTCOMES.map((o) => <button key={o.id} className={`btn sm ${outcome === o.id ? "primary" : ""}`} onClick={() => setOutcome(o.id)}>{o.label}</button>)}</div>
          <label className="field">Decision<input className="input" value={decision} placeholder="e.g. Ship the finetune checkpoint; keep the radar head" onChange={(e) => setDecision(e.target.value)} /></label>
          <label className="field">Based on (evidence, numbers, chapters)<textarea className="input" rows={3} value={rationale} onChange={(e) => setRationale(e.target.value)} /></label>
          {needsOverride && <label className="field">Override reason (required: the checks block a verdict)<input className="input" value={override} onChange={(e) => setOverride(e.target.value)} placeholder="e.g. compared only on the ruler-free per-class metrics" /></label>}
          <div className="row"><button className="btn primary" disabled={!decision.trim() || decide.isPending || (needsOverride && !override.trim())} onClick={() => decide.mutate()}><Gavel size={14} /> Record decision</button>
            <span className="faint">Exported with the journal (Markdown) and the study book.</span></div>
          {decide.error && <ErrorBox error={decide.error} />}
        </div>
      </Card>
    </div>
  );
}
