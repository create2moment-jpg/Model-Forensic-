import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { fmtNum, fmtPct } from "../lib/format";

/** ``val/PassengerCar_AverageRecall[task=sparse_detection]`` → name "PassengerCar_AverageRecall", detail "val · task=sparse_detection". */
export function splitKey(key: string): { name: string; detail: string } {
  const i = key.indexOf("[");
  const base = i < 0 ? key : key.slice(0, i);
  const dims = i < 0 ? "" : key.slice(i + 1, -1).replace(/,/g, " · ");
  const slash = base.indexOf("/");
  const split = slash < 0 ? "" : base.slice(0, slash);
  const name = slash < 0 ? base : base.slice(slash + 1);
  return { name, detail: [split, dims].filter(Boolean).join(" · ") };
}

/** A small, fixed-size trend line (no axes): where the metric started, where it went, where it ended. */
export function Sparkline({ values, width = 160, height = 28, color = "var(--accent)" }: { values: (number | null)[]; width?: number; height?: number; color?: string }) {
  const v = values.map((x) => (x === null || !Number.isFinite(x) ? null : x));
  const fin = v.filter((x): x is number => x !== null);
  if (fin.length < 2) return null;
  const lo = Math.min(...fin), hi = Math.max(...fin);
  const span = hi - lo || 1;
  const pts: string[] = [];
  v.forEach((x, i) => { if (x !== null) pts.push(`${((i / (v.length - 1)) * width).toFixed(1)},${(height - 2 - ((x - lo) / span) * (height - 4)).toFixed(1)}`); });
  const last = pts[pts.length - 1]!.split(",");
  return (
    <svg className="spark" width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
      <circle cx={last[0]} cy={last[1]} r={2} fill={color} />
    </svg>
  );
}

/** One headline value in a fixed tile: a short name (full key on hover), its split and dimensions underneath, the
 *  value, its trend over the run and the change since the start (green when it moved the right way). */
export function MetricTile({ k, value, trend, delta }: { k: string; value: number | undefined; trend?: (number | null)[]; delta?: React.ReactNode }) {
  const { name, detail } = splitKey(k);
  const fin = (trend ?? []).filter((x): x is number => x !== null && Number.isFinite(x));
  const first = fin[0], last = fin[fin.length - 1];
  // a change needs a history: a single evaluation value has none
  const change = fin.length >= 3 && first !== undefined && last !== undefined && first !== 0 ? (last - first) / Math.abs(first) : null;
  const lowerBetter = /loss|error|ate|ase|aoe|ave|aae|ece|ids/i.test(name);
  const good = change === null ? null : lowerBetter ? change < 0 : change > 0;
  return (
    <div className="card metric-tile" title={k} data-key={k}>
      <div className="mt-name trunc">{name}</div>
      <div className="mt-detail trunc mono">{detail || " "}</div>
      <div className="mt-value">{fmtNum(value)}</div>
      {delta && <div className="mt-delta">{delta}</div>}
      <div className="mt-trend">
        {trend ? <Sparkline values={trend} color={good === false ? "var(--bad)" : "var(--accent)"} /> : <div className="spark-empty" />}
        {change !== null && (
          <span className="mt-change" style={{ color: good ? "var(--good)" : "var(--bad)" }} title="change from the start of the run to its end">
            {change >= 0 ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}{fmtPct(change)}
          </span>
        )}
      </div>
    </div>
  );
}
