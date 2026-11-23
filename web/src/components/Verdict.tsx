import { AlertOctagon, AlertTriangle, ArrowRight, CheckCircle2, CircleDashed, Loader2, Radio, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { CompareVerdict, Verdict, VerdictNumber } from "../api/types";

const ICON: Record<Verdict["state"], ReactNode> = {
  healthy: <CheckCircle2 size={20} color="var(--good)" />,
  attention: <AlertTriangle size={20} color="var(--warn)" />,
  problem: <AlertOctagon size={20} color="var(--bad)" />,
  failed: <XCircle size={20} color="var(--bad)" />,
  running: <Radio size={20} color="var(--info)" />,
  not_loaded: <CircleDashed size={20} color="var(--text-3)" />,
  no_data: <CircleDashed size={20} color="var(--text-3)" />,
};

/** A tiny trend line (no axes): the shape of the curve, never its numbers — those are next to it. */
export function Spark({ values, tone = "var(--accent)", width = 96, height = 26 }: { values: number[]; tone?: string; width?: number; height?: number }) {
  if (values.length < 2) return <svg width={width} height={height} aria-hidden />;
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * (width - 2) + 1).toFixed(1)},${(height - 2 - ((v - lo) / span) * (height - 4)).toFixed(1)}`);
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden className="spark">
      <polyline points={pts.join(" ")} fill="none" stroke={tone} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={pts[pts.length - 1]!.split(",")[0]} cy={pts[pts.length - 1]!.split(",")[1]} r={2.2} fill={tone} />
    </svg>
  );
}

function Num({ h, onOpen }: { h: VerdictNumber; onOpen?: (keys: string[]) => void }) {
  const tone = h.good === true ? "var(--good)" : h.good === false ? "var(--bad)" : "var(--text-3)";
  return (
    <button type="button" className="verdict-num" onClick={() => onOpen?.([h.key])} title={`${h.key} — open its chart`} data-testid="verdict-number">
      <span className="verdict-num-label">{h.label}</span>
      <span className="row" style={{ gap: 10, alignItems: "flex-end" }}>
        <span className="verdict-num-value num">{h.text}</span>
        {h.delta_pct !== null && <span className="verdict-delta num" style={{ color: tone }} title={h.noise ? "within noise: smaller than a meaningful change for this metric" : "vs baseline"}>{h.delta_pct >= 0 ? "▲" : "▼"} {Math.abs(h.delta_pct).toFixed(1)}%{h.noise ? <span className="faint" style={{ fontWeight: 400 }}> · noise</span> : null}</span>}
      </span>
      <Spark values={h.spark} tone={h.good === false ? "var(--bad)" : "var(--accent)"} />
    </button>
  );
}

/** The run answered: one sentence, the headline numbers against the baseline, one next step. Built from the same
 *  analyses the Review shows — never a claim without evidence behind it. */
export function VerdictCard({ v, loading, onOpen, onNext, onLoad, onBaseline }: {
  v: Verdict | undefined; loading?: boolean; onOpen?: (keys: string[]) => void; onNext?: (n: NonNullable<Verdict["next"]>) => void;
  onLoad?: () => void; onBaseline?: () => void;
}) {
  if (!v) {
    return (
      <div className="card verdict skeleton-card" data-testid="verdict" data-state="loading">
        <div className="row" style={{ gap: 10 }}><Loader2 size={18} className="spin" color="var(--accent)" /><div className="sk sk-line" style={{ width: "46%" }} /></div>
        <div className="verdict-nums">{[0, 1, 2].map((i) => <div key={i} className="verdict-num sk-box" />)}</div>
      </div>
    );
  }
  return (
    <div className={`card verdict verdict-${v.state}`} data-testid="verdict" data-state={v.state}>
      <div className="verdict-head">
        <span className="verdict-icon">{loading ? <Loader2 size={20} className="spin" color="var(--accent)" /> : ICON[v.state]}</span>
        <div style={{ minWidth: 0 }}>
          <div className="verdict-sentence" data-testid="verdict-sentence">{v.sentence}{v.lead && <span className="verdict-lead"> {v.lead}</span>}</div>
          {v.detail && <div className="verdict-detail">{v.detail}</div>}
        </div>
      </div>
      {v.headline.length > 0 && <div className="verdict-nums">{v.headline.map((h) => <Num key={h.key} h={h} onOpen={onOpen} />)}</div>}
      <div className="verdict-foot">
        {v.baseline ? (
          <span className="faint" data-testid="verdict-baseline">vs <b className="mono" title={v.baseline.ref}>{v.baseline.name}</b>{v.baseline.why && v.baseline.why !== "chosen" ? ` · ${v.baseline.why}` : ""}{onBaseline && <> · <button type="button" className="linklike" onClick={onBaseline}>change</button></>}</span>
        ) : <span className="faint">No baseline yet{onBaseline && <> · <button type="button" className="linklike" onClick={onBaseline}>pick one</button></>}</span>}
        <span className="spacer" />
        {v.state === "not_loaded" && onLoad && <button className="btn sm primary" onClick={onLoad} data-testid="verdict-load">Read the run</button>}
        {v.next && <button className="btn sm verdict-next" onClick={() => onNext?.(v.next!)} data-testid="verdict-next" title="The one thing to do next">{v.next.text} <ArrowRight size={13} /></button>}
      </div>
    </div>
  );
}

/** A comparison answered in one sentence, with what changed in the config. */
export function CompareVerdictLine({ v }: { v: CompareVerdict | undefined }) {
  if (!v) return <div className="card verdict" data-testid="compare-verdict" data-state="loading"><div className="sk sk-line" style={{ width: "60%" }} /></div>;
  const icon = { better: <CheckCircle2 size={20} color="var(--good)" />, worse: <AlertOctagon size={20} color="var(--bad)" />, mixed: <AlertTriangle size={20} color="var(--warn)" />, same: <CircleDashed size={20} color="var(--text-3)" />, no_data: <CircleDashed size={20} color="var(--text-3)" /> }[v.state];
  return (
    <div className={`card verdict verdict-${v.state}`} data-testid="compare-verdict" data-state={v.state}>
      <div className="verdict-head">
        <span className="verdict-icon">{icon}</span>
        <div style={{ minWidth: 0 }}>
          <div className="verdict-sentence">{v.sentence}</div>
          <div className="verdict-detail mono-ish">{v.detail}</div>
        </div>
      </div>
    </div>
  );
}
