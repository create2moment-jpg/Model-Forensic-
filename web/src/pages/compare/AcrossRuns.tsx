/** Three or more runs: each headline metric across the runs, in the order they were picked (baseline first) — the
 *  progression the comparison tells, on the Compare page itself. From the indexed values only (no history load);
 *  the curves over steps stay one click away (Progression). */
import { useQueries } from "@tanstack/react-query";
import { ArrowRight, TrendingDown, TrendingUp } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { RunLight } from "../../api/types";
import { Card } from "../../components/ui";
import { MetricName } from "../../components/MetricTile";
import { fmtNum, fmtPct } from "../../lib/format";
import { lowerIsBetter, valueOf } from "./RunsOverview";

const W = 240;
const H = 76;
const AXIS = 12;  // run labels under the points: B, 1, 2, …
const PAD = 8;

export interface Trend { values: (number | undefined)[]; best: number; rel: number | null; direction: "better" | "worse" | "flat" | "mixed" }

/** The best run (index), its change against the baseline, and where the runs head overall (last vs baseline). */
export function trendOf(values: (number | undefined)[], lowerBetter: boolean): Trend | null {
  const known = values.map((v, i) => [v, i] as const).filter((x): x is readonly [number, number] => x[0] !== undefined && Number.isFinite(x[0]));
  if (known.length < 2) return null;
  const better = (a: number, b: number): boolean => (lowerBetter ? a < b : a > b);
  const best = known.reduce((m, x) => (better(x[0], m[0]) ? x : m))[1];
  const b = values[0];
  const rel = b !== undefined && b !== 0 && best !== 0 ? (values[best]! - b) / Math.abs(b) : null;
  const deltas = known.slice(1).map(([v]) => (b === undefined ? 0 : (v - b) / (Math.abs(b) || 1)));
  const small = 0.005;
  const up = deltas.filter((d) => (lowerBetter ? d < -small : d > small)).length;
  const down = deltas.filter((d) => (lowerBetter ? d > small : d < -small)).length;
  const direction = up && !down ? "better" : down && !up ? "worse" : !up && !down ? "flat" : "mixed";
  return { values, best, rel, direction };
}

function Spark({ values, best, lowerBetter }: { values: (number | undefined)[]; best: number; lowerBetter: boolean }) {
  const known = values.filter((v): v is number => v !== undefined && Number.isFinite(v));
  const lo = Math.min(...known);
  const hi = Math.max(...known);
  const span = hi - lo || Math.abs(hi) || 1;
  const x = (i: number): number => PAD + (i * (W - 2 * PAD)) / Math.max(1, values.length - 1);
  const y = (v: number): number => H - AXIS - PAD - ((v - lo) / span) * (H - AXIS - 2 * PAD);
  const pts = values.map((v, i) => (v === undefined || !Number.isFinite(v) ? null : ([x(i), y(v)] as const)));
  const path = pts.filter(Boolean).map((p, i) => `${i ? "L" : "M"}${p![0].toFixed(1)},${p![1].toFixed(1)}`).join(" ");
  const b = values[0];
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`values across ${values.length} runs${lowerBetter ? " (lower is better)" : ""}`} style={{ display: "block", maxWidth: "100%" }}>
      {b !== undefined && Number.isFinite(b) && <line x1={PAD} x2={W - PAD} y1={y(b)} y2={y(b)} stroke="var(--text-3)" strokeDasharray="3 3" strokeWidth={1} />}
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.6} strokeLinejoin="round" />
      {values.map((_, i) => <text key={`l${i}`} x={x(i)} y={H - 2} textAnchor="middle" fontSize={10} fill={i === best ? "var(--good)" : "var(--text-3)"}>{i === 0 ? "B" : i}</text>)}
      {pts.map((p, i) => p && (
        <circle key={i} cx={p[0]} cy={p[1]} r={i === best ? 4 : 2.6} fill={i === 0 ? "var(--bg-1, var(--bg))" : i === best ? "var(--good)" : "var(--accent)"}
          stroke={i === 0 ? "var(--text-2)" : "none"} strokeWidth={1.4}><title>{`${i === 0 ? "baseline" : `candidate ${i}`}: ${fmtNum(values[i])}`}</title></circle>
      ))}
    </svg>
  );
}

export function AcrossRuns({ baseline, candidates, name }: { baseline: string; candidates: string[]; name: (id: string) => string }) {
  const ids = [baseline, ...candidates];
  const qs = useQueries({ queries: ids.map((id) => ({ queryKey: ["run-light", id], queryFn: () => api.runLight(id) })) });
  if (qs.some((q) => !q.data)) return null;
  const lights = qs.map((q) => q.data as RunLight);
  const heads = [...new Set(lights.flatMap((l) => l.headline ?? []))];
  const rows = heads
    .map((k) => ({ k, lb: lowerIsBetter(k), values: lights.map((l) => valueOf(l, k)) }))
    .map((r) => ({ ...r, t: trendOf(r.values, r.lb) }))
    .filter((r): r is typeof r & { t: Trend } => r.t !== null)
    .slice(0, 4);
  const link = `/progression?runs=${ids.map(encodeURIComponent).join(",")}`;
  return (
    <Card title="Across the runs" sub={`${ids.length} runs in the order picked · baseline first (dashed)`} right={<Link className="btn ghost sm" to={link}>Curves over steps <ArrowRight size={13} /></Link>} flush>
      <div data-testid="across-runs">
        {rows.length === 0 ? <div className="card-body muted">No headline values indexed for these runs yet — <Link to={link}>see their curves</Link>.</div> : (
          <div className="across-grid">
            {rows.map(({ k, lb, values, t }) => (
              <div key={k} className="across-cell" data-metric={k}>
                <div className="row" style={{ gap: 6 }}>
                  <MetricName k={k} />
                  <span className="spacer" />
                  {t.direction === "better" ? <TrendingUp size={14} color="var(--good)" aria-label="every candidate is better" />
                    : t.direction === "worse" ? <TrendingDown size={14} color="var(--bad)" aria-label="every candidate is worse" /> : null}
                </div>
                <Spark values={values} best={t.best} lowerBetter={lb} />
                <div className="faint" style={{ fontSize: 12 }}>
                  {t.best === 0 ? "the baseline is best" : <>best: <b style={{ color: "var(--text)" }}>{name(ids[t.best]!)}</b> {fmtNum(values[t.best])}{t.rel !== null && <span style={{ color: "var(--good)" }}> {fmtPct(t.rel)}</span>}</>}
                  {lb ? " · lower is better" : ""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
