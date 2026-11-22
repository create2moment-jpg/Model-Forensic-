import { Bot, ChevronDown, ChevronRight, FlaskConical, HelpCircle, Lightbulb, Loader2, ThumbsDown, ThumbsUp, Wand } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, refStr } from "../api/client";
import type { Finding, NextExperiment } from "../api/types";
import { fmtNum } from "../lib/format";
import { ErrorBox, LabelPill, Pill, SeverityPill } from "./ui";

export function FindingCard({ f, defaultOpen = false, actions }: { f: Finding; defaultOpen?: boolean; actions?: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`finding ${f.set_aside ? "set-aside" : ""}`}>
      <div className="finding-head" onClick={() => setOpen(!open)}>
        <div className={`sev-bar sev-${f.severity}`} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="finding-title">{f.title}</span>
          </div>
          <div className="muted" style={{ marginTop: 3 }}>{f.statement}</div>
          {f.set_aside && <div className="aside-note"><Link to="/knowledge">Set aside by {f.set_aside.entry}</Link> — {f.set_aside.reason}</div>}
          <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
            <SeverityPill s={f.severity} />
            <LabelPill l={f.label} />
            <Pill tone="outline">confidence {f.confidence}</Pill>
            <span className="tag">{f.provenance.analysis}@{f.provenance.analysis_version}</span>
            {f.hypotheses.length > 0 && <span className="faint" style={{ fontSize: 12 }}>{f.hypotheses.length} hypotheses</span>}
            {f.recommendations.length > 0 && <span className="faint" style={{ fontSize: 12 }}>{f.recommendations.length} experiments</span>}
            {actions && <span className="row" style={{ gap: 4, marginLeft: "auto" }} onClick={(e) => e.stopPropagation()}>{actions}</span>}
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
          <Trust f={f} />
          <Copilot f={f} />
          <div className="faint mono" style={{ fontSize: 11 }}>
            {f.id} · params {f.provenance.params_hash} · runs {f.provenance.runs.map((r) => r.run_id).join(", ")}
          </div>
        </div>
      )}
    </div>
  );
}

/** "Why this confidence?" (deterministic explanation) and "this is wrong / correct" feedback. */
function Trust({ f }: { f: Finding }) {
  const [why, setWhy] = useState<{ why: string[]; would_change: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [askNote, setAskNote] = useState(false);
  const send = (v: "wrong" | "correct") => {
    if (v === "wrong" && !askNote) { setAskNote(true); return; }
    void api.findingFeedback(f.id, v, note).then(() => { setSent(v); setAskNote(false); }).catch(() => setSent("error"));
  };
  return (
    <div className="trust">
      <div className="row wrap" style={{ gap: 6 }}>
        <button className="btn sm ghost" onClick={() => { if (why) { setWhy(null); return; } setBusy(true); void api.explainFinding(f.id).then(setWhy).finally(() => setBusy(false)); }}>
          {busy ? <Loader2 size={13} className="spin" /> : <HelpCircle size={13} />} Why this confidence?
        </button>
        <span className="spacer" />
        {sent && sent !== "error" ? <span className="faint">Thanks — recorded as {sent}.</span> : (
          <>
            <span className="faint" style={{ fontSize: 12 }}>Is this finding right?</span>
            <button className="btn sm ghost" title="Correct" onClick={() => send("correct")}><ThumbsUp size={13} /></button>
            <button className="btn sm ghost" title="This is wrong" onClick={() => send("wrong")}><ThumbsDown size={13} /> {askNote ? "Send" : ""}</button>
          </>
        )}
      </div>
      {askNote && <input className="input" style={{ marginTop: 6 }} autoFocus placeholder="Optional: why is it wrong? (helps tune the thresholds)" value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") send("wrong"); }} />}
      {why && (
        <div className="why">
          <ul>{why.why.map((w) => <li key={w}>{w}</li>)}</ul>
          {why.would_change.length > 0 && <><div className="sub-h">What would change it</div><ul>{why.would_change.map((w) => <li key={w}>{w}</li>)}</ul></>}
        </div>
      )}
    </div>
  );
}

export const SEV_ORDER = { critical: 4, high: 3, medium: 2, low: 1, info: 0 } as const;
export const sortFindings = (fs: Finding[]): Finding[] => [...fs].sort((a, b) => Number(!!a.set_aside) - Number(!!b.set_aside) || SEV_ORDER[b.severity] - SEV_ORDER[a.severity]);

/** Ask the grounded investigator about this finding; propose the next run as a config diff (shown, never submitted). */
function Copilot({ f }: { f: Finding }) {
  const nav = useNavigate();
  const [busy, setBusy] = useState<"ask" | "next" | null>(null);
  const [next, setNext] = useState<NextExperiment | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const runs = f.provenance.runs;
  const run = runs.length > 1 ? runs[1] : runs[0];
  const ask = (): void => {
    setBusy("ask"); setErr(null);
    void api.askFinding(f.id).then((inv) => nav(`/ai?inv=${encodeURIComponent(inv.id)}${run ? `&run=${encodeURIComponent(refStr(run))}` : ""}`)).catch(setErr).finally(() => setBusy(null));
  };
  const propose = (): void => {
    setBusy("next"); setErr(null);
    void api.nextExperiment(f.id).then(setNext).catch(setErr).finally(() => setBusy(null));
  };
  return (
    <div>
      <div className="row" style={{ gap: 6 }}>
        <button className="btn sm" disabled={!!busy} onClick={ask}>{busy === "ask" ? <Loader2 size={13} className="spin" /> : <Bot size={13} />} Ask AI about this</button>
        <button className="btn sm" disabled={!!busy} onClick={propose}>{busy === "next" ? <Loader2 size={13} className="spin" /> : <Wand size={13} />} Next experiment</button>
      </div>
      {err ? <ErrorBox error={err} /> : null}
      {next && (
        <div className="next-exp">
          <div className="row" style={{ gap: 6 }}><b>{next.title}</b><Pill tone="outline">{next.source === "rule" ? "from the config diff" : next.source === "ai" ? "AI-proposed · keys validated" : "no config change found"}</Pill></div>
          {next.changes.length > 0 ? (
            <>
              <div className="table-wrap"><table className="t" style={{ marginTop: 6 }}><thead><tr><th>Config key</th><th>Now</th><th>Next run</th><th>Why</th></tr></thead>
                <tbody>{next.changes.map((c) => <tr key={c.path}><td className="mono">{c.path}</td><td className="mono">{JSON.stringify(c.current)}</td><td className="mono"><b>{JSON.stringify(c.proposed)}</b></td><td className="wrap">{c.why}</td></tr>)}</tbody></table></div>
              <pre className="mono" style={{ margin: "8px 0 0", fontSize: 12, whiteSpace: "pre-wrap" }}>{next.yaml}</pre>
            </>
          ) : <div className="muted">{next.expected_outcome || "Configure an LLM in Settings to get a proposed config change."}</div>}
          {next.rejected.length > 0 && <div className="faint" style={{ fontSize: 12 }}>Rejected invented keys: {next.rejected.join(", ")}</div>}
        </div>
      )}
    </div>
  );
}
