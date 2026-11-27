/** A pipeline as a graph, left to right: each step in the column after the steps that feed it, an arrow per
 *  connection (labelled output → input on hover). A step shows its status and time; clicking it opens it. */
import { Link } from "react-router-dom";
import type { PipelineEdge } from "../api/types";

export interface GraphStep { name: string; ref: string; status: string; duration_s: number | null; failure: string | null }

const W = 190, H = 58, GX = 110, GY = 22, PAD = 12;
const TONE: Record<string, string> = { completed: "var(--good)", failed: "var(--bad)", running: "var(--info)", canceled: "var(--warn)" };

const dur = (s: number | null): string => (s === null ? "" : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);

/** Column of each step: 0 for steps nothing feeds, else one after the latest step feeding it. */
export function layers(steps: string[], edges: PipelineEdge[]): Record<string, number> {
  const known = new Set(steps);
  const ins = new Map<string, string[]>(steps.map((s) => [s, []]));
  for (const e of edges) if (known.has(e.source) && known.has(e.target)) ins.get(e.target)!.push(e.source);
  const level: Record<string, number> = {};
  const visit = (s: string, seen: Set<string>): number => {
    if (level[s] !== undefined) return level[s]!;
    if (seen.has(s)) return 0;  // a cycle (never in a valid pipeline): do not loop
    seen.add(s);
    const l = Math.max(-1, ...ins.get(s)!.map((p) => visit(p, seen))) + 1;
    level[s] = l;
    return l;
  };
  steps.forEach((s) => visit(s, new Set()));
  return level;
}

export function PipelineGraph({ steps, edges }: { steps: GraphStep[]; edges: PipelineEdge[] }) {
  const lv = layers(steps.map((s) => s.name), edges);
  const cols: GraphStep[][] = [];
  steps.forEach((s) => { (cols[lv[s.name] ?? 0] ??= []).push(s); });
  const pos: Record<string, { x: number; y: number }> = {};
  cols.forEach((col, c) => col.forEach((s, r) => { pos[s.name] = { x: PAD + c * (W + GX), y: PAD + r * (H + GY) }; }));
  const width = PAD * 2 + cols.length * W + (cols.length - 1) * GX;
  const height = PAD * 2 + Math.max(...cols.map((c) => c.length)) * (H + GY) - GY;
  return (
    <div className="pipeline-graph" data-testid="pipeline-graph" style={{ position: "relative", width, height, maxWidth: "100%", overflowX: "auto" }}>
      <svg width={width} height={height} style={{ position: "absolute", inset: 0 }} aria-hidden>
        <defs><marker id="pg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="var(--text-3)" /></marker></defs>
        {edges.filter((e) => pos[e.source] && pos[e.target]).map((e, i) => {
          const a = pos[e.source]!, b = pos[e.target]!;
          const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 2, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
          return (
            <g key={i}>
              <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" stroke="var(--text-3)" strokeWidth={1.4} markerEnd="url(#pg-arrow)"><title>{`${e.source}.${e.output ?? "out"} → ${e.target}.${e.input ?? "in"}`}</title></path>
              {(e.output || e.input) && <text x={mx} y={(y1 + y2) / 2 - 6} textAnchor="middle" fontSize={10.5} fill="var(--text-3)">{[e.output, e.input].filter(Boolean).join(" → ")}</text>}
            </g>
          );
        })}
      </svg>
      {steps.map((s) => {
        const p = pos[s.name]!;
        return (
          <Link key={s.ref} to={`/runs/${encodeURIComponent(s.ref)}`} className="pg-node" data-status={s.status} title={s.failure ?? `${s.name} — open this step`}
            style={{ left: p.x, top: p.y, width: W, height: H, borderLeftColor: TONE[s.status] ?? "var(--line-2)" }}>
            <span className="mono ellipsis" style={{ fontWeight: 550 }}>{s.name}</span>
            <span className="faint" style={{ fontSize: 12 }}><span style={{ color: TONE[s.status] }}>{s.status}</span>{s.duration_s !== null ? ` · ${dur(s.duration_s)}` : ""}</span>
          </Link>
        );
      })}
    </div>
  );
}
