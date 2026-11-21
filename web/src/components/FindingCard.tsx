import { ChevronDown, ChevronRight, FlaskConical, Lightbulb } from "lucide-react";
import { useState } from "react";
import type { Finding } from "../api/types";
import { fmtNum } from "../lib/format";
import { LabelPill, Pill, SeverityPill } from "./ui";

export function FindingCard({ f, defaultOpen = false }: { f: Finding; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="finding">
      <div className="finding-head" onClick={() => setOpen(!open)}>
        <div className={`sev-bar sev-${f.severity}`} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="finding-title">{f.title}</span>
          </div>
          <div className="muted" style={{ marginTop: 3 }}>{f.statement}</div>
          <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
            <SeverityPill s={f.severity} />
            <LabelPill l={f.label} />
            <Pill tone="outline">confidence {f.confidence}</Pill>
            <span className="tag">{f.provenance.analysis}@{f.provenance.analysis_version}</span>
            {f.hypotheses.length > 0 && <span className="faint" style={{ fontSize: 12 }}>{f.hypotheses.length} hypotheses</span>}
            {f.recommendations.length > 0 && <span className="faint" style={{ fontSize: 12 }}>{f.recommendations.length} experiments</span>}
          </div>
        </div>
        {open ? <ChevronDown size={16} className="faint" /> : <ChevronRight size={16} className="faint" />}
      </div>
      {open && (
        <div className="finding-body">
          {f.evidence.length > 0 && (
            <div>
              <div className="sub-h">Evidence</div>
              <div className="col" style={{ gap: 6 }}>
                {f.evidence.map((e) => (
                  <div key={e.id} className="ev">
                    <LabelPill l={e.label} />
                    <span style={{ flex: 1 }}>{e.statement}</span>
                    {e.source.metric && <span className="mono faint">{e.source.metric}</span>}
                    {e.source.config_path && <span className="mono faint">{e.source.config_path}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {f.hypotheses.length > 0 && (
            <div>
              <div className="sub-h">Hypotheses — not established</div>
              <div className="col" style={{ gap: 6 }}>
                {f.hypotheses.map((h) => (
                  <div key={h.id} className="hyp">
                    <div className="row"><Lightbulb size={13} color="var(--warn)" /><span>{h.statement}</span><span className="spacer" /><Pill tone="outline">{h.confidence}</Pill></div>
                    {h.falsifiable_by && <div className="faint" style={{ fontSize: 12, marginTop: 4, paddingLeft: 21 }}>Falsify by: {h.falsifiable_by}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {f.recommendations.length > 0 && (
            <div>
              <div className="sub-h">Recommended experiments</div>
              <div className="col" style={{ gap: 6 }}>
                {f.recommendations.map((r) => (
                  <div key={r.id} className="prop">
                    <div className="row"><FlaskConical size={13} color="var(--good)" /><b>P{r.priority} · {r.title}</b><span className="spacer" />
                      <span className="faint" style={{ fontSize: 12 }}>info gain {r.information_gain.toFixed(2)} · {r.estimated_cost_gpu_hours ? `${fmtNum(r.estimated_cost_gpu_hours, 3)} GPU-h` : "cost n/a"}</span></div>
                    <dl className="kv" style={{ marginTop: 8, fontSize: 12 }}>
                      <dt>Controlled</dt><dd className="mono">{r.controlled_variable}</dd>
                      <dt>Fixed</dt><dd className="mono">{r.fixed_variables.join(", ")}</dd>
                      <dt>Measure</dt><dd>{r.measurements.join(", ")}</dd>
                      <dt>Expected</dt><dd>{r.expected_outcome}</dd>
                      <dt>Accept if</dt><dd>{r.acceptance_criterion}</dd>
                    </dl>
                  </div>
                ))}
              </div>
            </div>
          )}
          {f.limitations.length > 0 && (
            <div>
              <div className="sub-h">Limitations</div>
              <ul style={{ margin: 0, paddingLeft: 18 }} className="muted">{f.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
            </div>
          )}
          <div className="faint mono" style={{ fontSize: 11 }}>
            {f.id} · params {f.provenance.params_hash} · runs {f.provenance.runs.map((r) => r.run_id).join(", ")}
          </div>
        </div>
      )}
    </div>
  );
}

export const SEV_ORDER = { critical: 4, high: 3, medium: 2, low: 1, info: 0 } as const;
export const sortFindings = (fs: Finding[]): Finding[] => [...fs].sort((a, b) => SEV_ORDER[b.severity] - SEV_ORDER[a.severity]);
