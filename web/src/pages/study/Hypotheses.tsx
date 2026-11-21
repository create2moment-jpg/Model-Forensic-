import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, FlaskConical, HelpCircle, Lightbulb, Loader2, Plus, Trash2, X, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, refStr } from "../../api/client";
import type { HypothesisStatus, Study, StudyHypothesis, VerifyResult } from "../../api/types";
import { RunPicker } from "../../components/RunPicker";
import { Card, Empty, ErrorBox, Pill } from "../../components/ui";
import { fmtNum, fmtPct } from "../../lib/format";

const COLUMNS: { id: HypothesisStatus; label: string; icon: React.ReactNode; tone: string }[] = [
  { id: "open", label: "Open", icon: <CircleDashed size={14} />, tone: "info" },
  { id: "supported", label: "Supported", icon: <CheckCircle2 size={14} color="var(--good)" />, tone: "good" },
  { id: "inconclusive", label: "Inconclusive", icon: <HelpCircle size={14} color="var(--warn)" />, tone: "warn" },
  { id: "rejected", label: "Rejected", icon: <XCircle size={14} color="var(--bad)" />, tone: "bad" },
];

export function HypothesesTab({ study, onChange, onNew }: { study: Study; onChange: (s: Study) => void; onNew: () => void }) {
  if (study.hypotheses.length === 0) {
    return <Card><Empty icon={<Lightbulb size={28} />} title="No hypotheses yet"
      action={<button className="btn primary" onClick={onNew}><Plus size={14} /> New hypothesis</button>}>
      Turn a finding (Evidence tab) or an AI claim (AI tab) into a hypothesis, or write your own. Each one tracks its evidence, the
      architecture change it suggests, and the run that tests it.</Empty></Card>;
  }
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row"><span className="faint">Update each hypothesis' status as evidence comes in. Verify compares its test run with the run where the problem was observed.</span>
        <span className="spacer" /><button className="btn primary sm" onClick={onNew}><Plus size={13} /> New hypothesis</button></div>
      {COLUMNS.map((col) => {
        const hs = study.hypotheses.filter((h) => h.status === col.id);
        if (!hs.length) return null;
        return (
          <div key={col.id} className="col" style={{ gap: 10 }}>
            <div className="row" style={{ padding: "6px 4px 0" }}>{col.icon}<b>{col.label}</b><span className="faint">{hs.length}</span></div>
            {hs.map((h) => <HypothesisCard key={h.id} study={study} h={h} onChange={onChange} />)}
          </div>
        );
      })}
    </div>
  );
}

function HypothesisCard({ study, h, onChange }: { study: Study; h: StudyHypothesis; onChange: (s: Study) => void }) {
  const [verdict, setVerdict] = useState(h.verdict);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const patch = useMutation({ mutationFn: (body: Parameters<typeof api.studyPatchHypothesis>[2]) => api.studyPatchHypothesis(study.id, h.id, body), onSuccess: onChange });
  const del = useMutation({ mutationFn: () => api.studyDeleteHypothesis(study.id, h.id), onSuccess: onChange });
  const rmEv = useMutation({ mutationFn: ({ kind, ref }: { kind: string; ref: string }) => api.studyRemoveEvidence(study.id, h.id, kind, ref), onSuccess: onChange });
  const ver = useMutation({ mutationFn: () => api.studyVerify(study.id, h.id), onSuccess: setVerify });
  const moved = verify ? verify.comparison.deltas.filter((d) => d.verdict === "improved" || d.verdict === "regressed")
    .sort((a, b) => Math.abs(b.relative ?? 0) - Math.abs(a.relative ?? 0)).slice(0, 8) : [];

  const tone = COLUMNS.find((c) => c.id === h.status)?.tone ?? "info";
  return (
    <div className="card" style={{ padding: 14, borderLeft: `3px solid var(--${tone === "info" ? "info" : tone})` }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(0, 1fr)", gap: 18 }}>
      <div>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1, fontWeight: 600, fontSize: 14 }}>{h.statement}</div>
        <button className="btn ghost sm icon" title="Delete" onClick={() => confirm("Delete this hypothesis?") && del.mutate()}><Trash2 size={12} /></button>
      </div>
      {h.rationale && <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>{h.rationale}</div>}
      {h.evidence.length > 0 && (
        <div className="row wrap" style={{ gap: 4, marginTop: 8 }}>
          {h.evidence.map((e) => (
            <span key={e.kind + e.ref} className="tag row" style={{ gap: 4, color: e.supports ? undefined : "var(--bad)" }} title={`${e.kind}: ${e.ref}`}>
              {e.kind === "claim" ? "AI" : e.kind}: <span className="ellipsis" style={{ maxWidth: 170 }}>{e.label}</span>
              <X size={10} style={{ cursor: "pointer" }} onClick={() => rmEv.mutate({ kind: e.kind, ref: e.ref })} />
            </span>
          ))}
        </div>
      )}
      {h.proposed_change && <div className="prop" style={{ marginTop: 8, fontSize: 12.5 }}><FlaskConical size={12} color="var(--good)" /> {h.proposed_change}</div>}
      </div>
      <div>
      <div className="col" style={{ gap: 6, fontSize: 12 }}>
        <div className="row"><span className="faint" style={{ width: 70 }}>Observed</span>
          <span className="mono ellipsis">{h.reference_run ? h.reference_run.run_id : study.baseline ? `${study.baseline.run_id} (baseline)` : "—"}</span></div>
        <div className="row"><span className="faint" style={{ width: 70 }}>Test run</span>
          {h.test_run ? <><Link className="mono ellipsis" style={{ color: "var(--accent-2)" }} to={`/runs/${encodeURIComponent(refStr(h.test_run))}`}>{h.test_run.run_id}</Link>
            <button className="btn ghost sm icon" title="Unlink" onClick={() => patch.mutate({ test_run: "" })}><X size={11} /></button></>
            : <RunPicker value={null} onChange={(v) => patch.mutate({ test_run: v })} placeholder="Link the run that tests it…" width={260} />}
        </div>
      </div>
      {h.test_run && (
        <button className="btn sm" style={{ marginTop: 8, width: "100%", justifyContent: "center" }} disabled={ver.isPending} onClick={() => ver.mutate()}>
          {ver.isPending ? <Loader2 size={12} className="spin" /> : <FlaskConical size={12} />} Verify against reference
        </button>
      )}
      {ver.error && <div style={{ marginTop: 8 }}><ErrorBox error={ver.error} /></div>}
      {verify && (
        <div className="note" style={{ marginTop: 8, display: "block", fontSize: 12 }}>
          <div>{verify.suggestion}</div>
          <table className="t compact" style={{ marginTop: 6 }}>
            <tbody>{moved.map((d) => {
              const worse = d.higher_is_better === null || d.relative === null ? null : d.higher_is_better ? d.relative < 0 : d.relative > 0;
              return <tr key={d.metric}><td className="mono wrap" style={{ fontSize: 11 }}>{d.metric}</td><td className="r num" style={{ fontSize: 11 }}>{fmtNum(d.candidate)}</td>
                <td className="r num" style={{ fontSize: 11, color: worse ? "var(--bad)" : "var(--good)" }}>{fmtPct(d.relative)}</td></tr>;
            })}</tbody>
          </table>
          {moved.length === 0 && <div className="faint">No metric moved beyond noise.</div>}
        </div>
      )}
      <div className="sep" style={{ margin: "10px 0" }} />
      <input className="input" style={{ width: "100%", height: 28, fontSize: 12 }} placeholder="Verdict / conclusion…" value={verdict}
        onChange={(e) => setVerdict(e.target.value)} onBlur={() => verdict !== h.verdict && patch.mutate({ verdict })} />
      <div className="row wrap" style={{ gap: 4, marginTop: 8 }}>
        <Pill tone={tone}>{h.status}</Pill>
        {COLUMNS.filter((c) => c.id !== h.status).map((c) => (
          <button key={c.id} className="btn sm" style={{ fontSize: 11.5 }} disabled={patch.isPending} onClick={() => patch.mutate({ status: c.id, verdict })}>
            → {c.label}
          </button>
        ))}
        <span className="spacer" />
        <span className="faint" style={{ fontSize: 11 }}>from {h.source.split(":")[0]}</span>
      </div>
      </div>
      </div>
    </div>
  );
}
