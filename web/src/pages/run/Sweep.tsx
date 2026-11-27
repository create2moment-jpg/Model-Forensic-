/** A sweep's trials, as studio's sweep view: the best trial in one sentence, parallel coordinates (one axis per
 *  hyper-parameter that varies, the primary metric last; the best trial drawn on top), and the trials ranked in the
 *  goal's direction. Trial values missing from the listing are loaded once, in the background. */
import { useQuery } from "@tanstack/react-query";
import { Trophy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { SweepView } from "../../api/types";
import { Card, StatusPill } from "../../components/ui";
import { fmtNum } from "../../lib/format";

const fmtP = (v: number | string | undefined): string => (v === undefined ? "—" : typeof v === "number" ? (Math.abs(v) < 1e-2 && v !== 0 ? v.toExponential(0) : String(+v.toPrecision(4))) : v);

/** A scale for one axis: numbers (log when they span more than 20× and are all positive) or categories. */
function axis(values: (number | string | undefined)[]): (v: number | string | undefined) => number | null {
  const nums = values.filter((v): v is number => typeof v === "number");
  if (nums.length === values.filter((v) => v !== undefined).length && nums.length) {
    const lo = Math.min(...nums), hi = Math.max(...nums);
    const log = lo > 0 && hi / lo > 20;
    const f = (x: number): number => (log ? Math.log10(x) : x);
    const a = f(lo), b = f(hi);
    return (v) => (typeof v === "number" ? (b === a ? 0.5 : (f(v) - a) / (b - a)) : null);
  }
  const cats = [...new Set(values.filter((v) => v !== undefined).map(String))].sort();
  return (v) => (v === undefined ? null : cats.length < 2 ? 0.5 : cats.indexOf(String(v)) / (cats.length - 1));
}

function Parallel({ v }: { v: SweepView }) {
  const nav = useNavigate();
  const axes = [...v.varying, "__metric"];
  const trials = v.trials.filter((t) => t.value !== null);
  if (axes.length < 2 || trials.length < 2) return null;
  const W = 900, H = 260, PX = 70, PY = 26;
  const x = (i: number): number => PX + (i * (W - 2 * PX)) / (axes.length - 1);
  const scales = axes.map((a) => axis(trials.map((t) => (a === "__metric" ? t.value ?? undefined : t.params[a]))));
  const vals = trials.map((t) => t.value!);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const good = (val: number): number => (hi === lo ? 1 : v.higher_is_better === false ? (hi - val) / (hi - lo) : (val - lo) / (hi - lo));
  const ordered = [...trials].sort((a, b) => (a.ref === v.best ? 1 : b.ref === v.best ? -1 : good(a.value!) - good(b.value!)));
  const y = (s: number): number => H - PY - s * (H - 2 * PY);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img" aria-label="Parallel coordinates of the trials" data-testid="sweep-parallel">
      {axes.map((a, i) => (
        <g key={a}>
          <line x1={x(i)} x2={x(i)} y1={PY} y2={H - PY} stroke="var(--line-2, var(--line))" />
          <text x={x(i)} y={14} textAnchor="middle" fontSize={12} fill="var(--text-2)">{a === "__metric" ? v.label ?? "metric" : a}</text>
        </g>
      ))}
      {ordered.map((t) => {
        const pts = axes.map((a, i) => { const s = scales[i]!(a === "__metric" ? t.value ?? undefined : t.params[a]); return s === null ? null : `${x(i)},${y(s)}`; });
        if (pts.some((p) => p === null)) return null;
        const best = t.ref === v.best;
        const g = good(t.value!);
        return (
          <polyline key={t.ref} points={pts.join(" ")} fill="none" stroke={best ? "var(--good)" : "var(--accent)"} strokeOpacity={best ? 1 : 0.25 + 0.6 * g}
            strokeWidth={best ? 3 : 1.6} style={{ cursor: "pointer" }} onClick={() => nav(`/runs/${encodeURIComponent(t.ref)}`)}>
            <title>{`${t.name}: ${v.label} ${fmtNum(t.value)} · ${v.varying.map((k) => `${k}=${fmtP(t.params[k])}`).join(", ")}`}</title>
          </polyline>
        );
      })}
      {axes.map((a, i) => {
        const vals2 = a === "__metric" ? [lo, hi] : [...new Set(trials.map((t) => t.params[a]))];
        return vals2.map((val) => { const s = scales[i]!(val as number | string); return s === null ? null : <text key={`${a}:${val}`} x={x(i) - 6} y={y(s) + 4} textAnchor="end" fontSize={10.5} fill="var(--text-3)">{a === "__metric" ? fmtNum(val as number) : fmtP(val)}</text>; });
      })}
    </svg>
  );
}

export function SweepCard({ runId }: { runId: string }) {
  const [loading, setLoading] = useState(0);
  const q = useQuery({ queryKey: ["sweep", runId], queryFn: () => api.sweep(runId),
    refetchInterval: (x) => ((loading && Date.now() - loading < 120_000 && (x.state.data?.missing_values.length ?? 0) > 0) || (x.state.data?.counts.running ?? 0) > 0 ? 5000 : false) });
  const started = useRef("");
  const missing = q.data?.missing_values ?? [];
  useEffect(() => {  // trial values the listing lacks (Azure): load them once
    if (!missing.length || started.current === runId) return;
    started.current = runId;
    setLoading(Date.now());
    void api.fillSummaries(missing).catch(() => setLoading(0));
  }, [runId, missing]);
  const v = q.data;
  if (!v || v.counts.trials === 0) return null;
  const best = v.trials.find((t) => t.ref === v.best);
  const c = v.counts;
  return (
    <Card title={<span className="row"><Trophy size={15} /> Trials</span>} sub={`${c.trials} trials · ${c.completed} finished${c.running ? ` · ${c.running} running` : ""}${c.failed ? ` · ${c.failed} failed` : ""}${c.canceled ? ` · ${c.canceled} stopped early` : ""}${v.metric ? ` · ${v.goal ?? (v.higher_is_better ? "maximize" : "minimize")} ${v.label}` : ""}`} flush>
      <div data-testid="sweep">
        {best && <div className="card-body" data-testid="sweep-best" style={{ paddingBottom: 4 }}>
          Best: <Link to={`/runs/${encodeURIComponent(best.ref)}`}><b>{best.name}</b></Link> — {v.label} <b className="num">{fmtNum(best.value)}</b>
          {v.varying.length > 0 && <> with {v.varying.map((k) => <span key={k} className="mono" style={{ marginLeft: 6 }}>{k}={fmtP(best.params[k])}</span>)}</>}
        </div>}
        {missing.length > 0 && <div className="card-body faint" style={{ paddingTop: 4, paddingBottom: 4 }}>Loading the values of {missing.length} trial{missing.length === 1 ? "" : "s"}…</div>}
        <div style={{ padding: "4px 16px 8px" }}><Parallel v={v} /></div>
        <div className="table-wrap">
          <table className="t" data-testid="sweep-trials">
            <thead><tr><th>#</th><th>Trial</th><th>Status</th>{v.params.map((k) => <th key={k} className="r">{k}</th>)}<th className="r">{v.label ?? "metric"}</th><th className="r">Run time</th></tr></thead>
            <tbody>
              {v.trials.map((t, i) => (
                <tr key={t.ref} data-best={t.ref === v.best || undefined}>
                  <td className="faint num">{t.value !== null ? i + 1 : "—"}</td>
                  <td><Link to={`/runs/${encodeURIComponent(t.ref)}`} className="row" style={{ gap: 6 }}>{t.ref === v.best && <Trophy size={13} color="var(--good)" aria-label="best trial" />}{t.name}</Link></td>
                  <td><StatusPill status={t.status as never} /></td>
                  {v.params.map((k) => <td key={k} className={`r num ${v.varying.includes(k) ? "" : "faint"}`}>{fmtP(t.params[k])}</td>)}
                  <td className="r num" style={{ fontWeight: t.ref === v.best ? 600 : undefined }}>{fmtNum(t.value)}</td>
                  <td className="r num muted">{t.run_s === null ? "—" : `${Math.round(t.run_s / 60)} min`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}
