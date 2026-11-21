import { Lightbulb, X } from "lucide-react";
import { useState } from "react";
import type { EvidenceLink } from "../../api/types";

export interface HypothesisDraft {
  statement: string;
  rationale?: string;
  proposed_change?: string;
  evidence?: Partial<EvidenceLink>[];
  source?: string;
  reference_run?: string | null;
}

/** Modal to create a hypothesis — prefilled when it comes from a finding or an AI claim. */
export function HypothesisDialog({ draft, runs, onCancel, onSave, saving }: {
  draft: HypothesisDraft; runs: string[]; onCancel: () => void; onSave: (d: HypothesisDraft) => void; saving: boolean;
}) {
  const [d, setD] = useState<HypothesisDraft>(draft);
  return (
    <div className="overlay" onMouseDown={onCancel}>
      <div className="palette" style={{ width: 640 }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="card-head"><Lightbulb size={15} color="var(--warn)" /><h3>New hypothesis</h3><div className="right"><button className="btn ghost sm icon" onClick={onCancel}><X size={14} /></button></div></div>
        <div className="col" style={{ gap: 12, padding: 16 }}>
          <label className="field">Hypothesis *<textarea className="input" rows={2} autoFocus value={d.statement} onChange={(e) => setD({ ...d, statement: e.target.value })}
            placeholder="The radar branch is starved by its small learning-rate multiplier" /></label>
          <label className="field">Why do you think so?<textarea className="input" rows={2} value={d.rationale ?? ""} onChange={(e) => setD({ ...d, rationale: e.target.value })} /></label>
          <label className="field">Architecture / config change it suggests<input className="input" value={d.proposed_change ?? ""} onChange={(e) => setD({ ...d, proposed_change: e.target.value })}
            placeholder="radar lr_mult 0.02 → 1.0, add per-scale LayerNorm before fusion" /></label>
          <label className="field">Observed in run
            <select className="select" value={d.reference_run ?? ""} onChange={(e) => setD({ ...d, reference_run: e.target.value || null })}>
              <option value="">(study baseline)</option>
              {runs.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          {(d.evidence ?? []).length > 0 && (
            <div>
              <div className="sub-h">Evidence attached</div>
              <div className="row wrap" style={{ gap: 6 }}>{d.evidence!.map((e) => <span key={`${e.kind}:${e.ref}`} className="tag">{e.kind}: {e.label}</span>)}</div>
            </div>
          )}
          <div className="row"><span className="spacer" /><button className="btn" onClick={onCancel}>Cancel</button>
            <button className="btn primary" disabled={!d.statement.trim() || saving} onClick={() => onSave(d)}>Add hypothesis</button></div>
        </div>
      </div>
    </div>
  );
}
