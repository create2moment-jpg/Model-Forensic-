import { useQuery } from "@tanstack/react-query";
import { BookA, ChevronDown, ChevronRight, HeartPulse, Loader2, X } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { CheckCard } from "../api/types";
import { RunPicker } from "../components/RunPicker";
import { Card, ErrorBox } from "../components/ui";

/** Beginner path: pick a run (default: the newest completed), get at most five plain-language cards. */
export function CheckPage() {
  const [sp, setSp] = useSearchParams();
  const run = sp.get("run");
  const [glossary, setGlossary] = useState(false);
  const q = useQuery({ queryKey: ["check", run], queryFn: () => api.check(run) });
  const terms = useQuery({ queryKey: ["glossary"], queryFn: api.glossary, enabled: glossary });
  return (
    <div className="page" style={{ maxWidth: 1000 }}>
      <div className="page-head">
        <div>
          <h1 className="row"><HeartPulse size={20} color="var(--accent)" /> Check my run</h1>
          <div className="sub">The most important things to know about one run, in plain language. No setup, no jargon — details are one click away.</div>
        </div>
        <div className="actions">
          <div style={{ width: 300 }}><RunPicker value={run} onChange={(r) => setSp(r ? { run: r } : {})} placeholder="Newest completed run (or pick one)" /></div>
          <button className="btn" onClick={() => setGlossary(true)}><BookA size={14} /> Glossary</button>
        </div>
      </div>
      {q.isLoading && <div className="faint row"><Loader2 size={14} className="spin" /> Checking training, evaluation and setup…</div>}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <div className="col" style={{ gap: 12 }}>
          <Card>
            <div className="row" style={{ gap: 12 }}>
              <span className={`light-dot ${q.data.overall}`} style={{ width: 18, height: 18 }} />
              <div>
                <div style={{ fontSize: 17, fontWeight: 600 }}>{q.data.summary}</div>
                <div className="faint">
                  <Link to={`/runs/${encodeURIComponent(q.data.run.ref)}`}>{q.data.run.name}</Link>
                  {q.data.baseline && <> · compared with <span className="mono">{q.data.baseline}</span></>}
                  {" "}· {q.data.checked.length} checks ran
                </div>
              </div>
            </div>
          </Card>
          {q.data.cards.map((c, i) => <CheckCardView key={i} c={c} glossary={q.data.glossary} />)}
          {(q.data.knowledge?.length ?? 0) > 0 && (
            <div className="note">{q.data.knowledge!.map((k) => <div key={k.entry}>ⓘ {k.text} <Link to="/knowledge" className="faint">({k.entry})</Link></div>)}</div>
          )}
          {(q.data.set_aside?.length ?? 0) > 0 && (
            <details className="faint"><summary>{q.data.set_aside!.length} finding(s) set aside by the team knowledge</summary>
              <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>{q.data.set_aside!.map((x) => <li key={x.title}>{x.title} — {x.reason} (<Link to="/knowledge">{x.entry}</Link>)</li>)}</ul></details>
          )}
          {q.data.more > 0 && (
            <div className="faint">{q.data.more} more finding(s) — see the <Link to={`/runs/${encodeURIComponent(q.data.run.ref)}?tab=findings&analyze=1`}>Findings tab</Link> (standard mode).</div>
          )}
        </div>
      )}
      {glossary && (
        <div className="drawer" style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 420, background: "var(--bg-1, var(--bg))", borderLeft: "1px solid var(--line)", padding: 18, overflow: "auto", zIndex: 50 }}>
          <div className="row"><b style={{ fontSize: 16 }}>Glossary</b><span className="spacer" /><button className="btn ghost icon" onClick={() => setGlossary(false)} aria-label="Close"><X size={16} /></button></div>
          {Object.entries(terms.data ?? {}).map(([t, d]) => <div key={t} style={{ margin: "12px 0" }}><b>{t}</b><div className="muted">{d}</div></div>)}
        </div>
      )}
    </div>
  );
}

function CheckCardView({ c, glossary }: { c: CheckCard; glossary: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`card check-card ${c.light}`} style={{ padding: 16 }}>
      <div className="row" style={{ gap: 10 }}><span className={`light-dot ${c.light}`} /><b style={{ fontSize: 15 }}>{c.title}</b></div>
      <p style={{ margin: "8px 0 0" }}>{c.meaning}</p>
      <div className="check-grid">
        <div><div className="lbl">Why it matters</div>{c.why}</div>
        <div><div className="lbl">What to do next</div>{c.todo}</div>
      </div>
      {(c.learn_more.length > 0 || c.terms.length > 0) && (
        <button className="btn ghost sm" style={{ marginTop: 8 }} onClick={() => setOpen(!open)}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Learn more</button>
      )}
      {open && (
        <div className="col" style={{ gap: 6, marginTop: 6 }}>
          {c.learn_more.map((x, i) => <div key={i} className="muted" style={{ fontSize: 13 }}>• {x}</div>)}
          {c.terms.filter((t) => glossary[t]).map((t) => <div key={t} style={{ fontSize: 13 }}><b>{t}</b>: <span className="muted">{glossary[t]}</span></div>)}
        </div>
      )}
    </div>
  );
}
