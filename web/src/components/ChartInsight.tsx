import { AlertTriangle, Bot, CheckCircle2, Loader2, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { ChartAnswer, ChartReading, RunsReading } from "../api/types";
import { fmtNum } from "../lib/format";
import { Chart, type ChartMarker, type ChartSeries } from "./Chart";

const MARK_COLOR: Record<string, string> = {
  best: "#3ccf8e", plateau: "#4fa9ff", spike: "#ff5d73", dip: "#ff5d73", nan: "#ff5d73", lr_drop: "#f5b84b",
};

/** A metric chart with "Explain this chart" (deterministic reading + markers) and "Ask AI about this chart". */
export function ExplainableChart({ runId, metric, baseline, series, nonFinite = 0, ...chart }: {
  runId: string; metric: string; baseline?: string | null; series: ChartSeries[]; nonFinite?: number;
  smoothing?: number; logY?: boolean; syncKey?: string; height?: number;
}) {
  const [reading, setReading] = useState<ChartReading | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<ChartAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const toggle = () => {
    if (reading) { setReading(null); setAnswer(null); return; }
    setBusy(true); setErr(null);
    api.explainChart(runId, metric, baseline).then(setReading).catch((e: Error) => setErr(e.message)).finally(() => setBusy(false));
  };
  const ask = () => {
    setAsking(true);
    api.askChart(runId, metric, question, baseline).then(setAnswer).catch((e: Error) => setErr(e.message)).finally(() => setAsking(false));
  };
  const markers: ChartMarker[] = (reading?.markers ?? []).map((m) => ({ x: m.step, label: m.label, color: MARK_COLOR[m.kind] }));
  return (
    <div>
      <Chart
        title={metric} series={series} markers={markers} {...chart}
        badge={nonFinite > 0 ? <span className="pill bad" title="NaN / Inf values are not drawn">{nonFinite} NaN/Inf</span> : undefined}
        tools={<button className={`btn ghost sm ${reading ? "active" : ""}`} title="Explain this chart" onClick={toggle}>{busy ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />} Explain</button>}
      />
      {err && <div className="faint" style={{ padding: "0 12px 8px" }}>{err}</div>}
      {reading && (
        <div className="chart-insight">
          <div className="row" style={{ gap: 6 }}>
            <b>What this chart shows</b>
            <span className="faint">{reading.direction === "unknown" ? "direction unknown" : `${reading.direction} is better`} · {reading.points} points · deterministic</span>
            <span className="spacer" />
            <button className="btn ghost sm icon" title="Close" onClick={toggle}><X size={13} /></button>
          </div>
          {reading.warnings.map((w) => <div key={w} className="ci-line warn"><AlertTriangle size={13} />{w}</div>)}
          {reading.facts.map((f) => <div key={f} className="ci-line"><CheckCircle2 size={13} />{f}</div>)}
          {reading.checks.length > 0 && (
            <div className="ci-checks"><div className="sub-h">What to check</div>
              <ol>{reading.checks.map((c) => <li key={c}>{c}</li>)}</ol></div>
          )}
          {reading.markers.length > 0 && (
            <div className="row wrap faint" style={{ gap: 10, fontSize: 12 }}>
              {[...new Set(reading.markers.map((m) => m.kind))].map((k) => <span key={k} className="row" style={{ gap: 4 }}><i className="ci-dot" style={{ background: MARK_COLOR[k] ?? "#888" }} />{k.replace("_", " ")}</span>)}
            </div>
          )}
          <div className="row" style={{ gap: 6, marginTop: 4 }}>
            <input className="input" placeholder="Ask AI about this chart — e.g. is this healthy? what should I check?" value={question}
              onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") ask(); }} />
            <button className="btn sm" onClick={ask} disabled={asking}>{asking ? <Loader2 size={13} className="spin" /> : <Bot size={13} />} Ask</button>
          </div>
          {answer && <Answer a={answer} />}
        </div>
      )}
    </div>
  );
}

function Answer({ a }: { a: ChartAnswer }) {
  return (
    <div className="ci-answer">
      <div>{a.summary}</div>
      <ul>{a.claims.map((c, i) => (
        <li key={i} className={c.verified ? "" : "unverified"}>
          {c.type !== "FACT" && <b>{c.type.toLowerCase()}: </b>}{c.text} <span className="faint mono">[{c.evidence.join(", ")}]</span>
          {!c.verified && <span className="pill bad" style={{ marginLeft: 6 }}>unverified</span>}
        </li>
      ))}</ul>
      <div className="faint" style={{ fontSize: 11 }}>{a.model} · grounded on this chart's reading only</div>
    </div>
  );
}

/** Multi-run chart (Progression) with a cross-run reading: ranking, spread vs noise, NaN/diverged runs. */
export function ExplainableRunsChart({ runs, metric, series, ...chart }: {
  runs: string[]; metric: string; series: ChartSeries[]; smoothing?: number; showRaw?: boolean; logY?: boolean; syncKey?: string; height?: number; xLabel?: string;
}) {
  const [reading, setReading] = useState<RunsReading | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<ChartAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const toggle = () => {
    if (reading) { setReading(null); setAnswer(null); return; }
    setBusy(true); setErr(null);
    api.explainRuns(metric, runs).then(setReading).catch((e: Error) => setErr(e.message)).finally(() => setBusy(false));
  };
  const ask = () => {
    setAsking(true);
    api.askRuns(metric, runs, question).then(setAnswer).catch((e: Error) => setErr(e.message)).finally(() => setAsking(false));
  };
  return (
    <div>
      <Chart title={metric} series={series} {...chart}
        tools={runs.length >= 2 ? <button className={`btn ghost sm ${reading ? "active" : ""}`} title="Explain this chart" onClick={toggle}>{busy ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />} Explain</button> : undefined} />
      {err && <div className="faint" style={{ padding: "0 12px 8px" }}>{err}</div>}
      {reading && (
        <div className="chart-insight">
          <div className="row" style={{ gap: 6 }}>
            <b>Across {reading.runs} runs</b>
            <span className="faint">{reading.direction === "unknown" ? "direction unknown" : `${reading.direction} is better`} · deterministic</span>
            <span className="spacer" />
            <button className="btn ghost sm icon" title="Close" onClick={toggle}><X size={13} /></button>
          </div>
          {reading.warnings.map((w) => <div key={w} className="ci-line warn"><AlertTriangle size={13} />{w}</div>)}
          {reading.facts.map((f) => <div key={f} className="ci-line"><CheckCircle2 size={13} />{f}</div>)}
          {reading.ranking.length > 0 && (
            <div className="table-wrap"><table className="t compact"><thead><tr><th>#</th><th>Run</th><th className="r">Final</th><th className="r">Best</th><th className="r">Flat from step</th></tr></thead>
              <tbody>{reading.ranking.slice(0, 8).map((r, i) => (
                <tr key={r.run}><td className="faint">{i + 1}</td><td className="mono">{r.run}</td><td className="r num">{fmtNum(r.final)}</td>
                  <td className="r num">{r.best == null ? "—" : fmtNum(r.best)}</td><td className="r num">{r.plateau_step == null ? "—" : Math.round(r.plateau_step).toLocaleString()}</td></tr>
              ))}</tbody></table></div>
          )}
          {reading.checks.length > 0 && <div className="ci-checks"><div className="sub-h">What to check</div><ol>{reading.checks.map((c) => <li key={c}>{c}</li>)}</ol></div>}
          <div className="row" style={{ gap: 6, marginTop: 4 }}>
            <input className="input" placeholder="Ask AI about these runs — e.g. which change helped? is the winner real?" value={question}
              onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") ask(); }} />
            <button className="btn sm" onClick={ask} disabled={asking}>{asking ? <Loader2 size={13} className="spin" /> : <Bot size={13} />} Ask</button>
          </div>
          {answer && <Answer a={answer} />}
        </div>
      )}
    </div>
  );
}
