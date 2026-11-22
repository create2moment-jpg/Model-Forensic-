import { Search, X } from "lucide-react";

/** ``val/AP[class=car,dist=40]`` → family "val/AP", dimensions {class: car, dist: 40}. */
export function parseMetric(k: string): { family: string; dims: Record<string, string> } {
  const i = k.indexOf("[");
  if (i < 0) return { family: k, dims: {} };
  return { family: k.slice(0, i), dims: Object.fromEntries(k.slice(i + 1, -1).split(",").filter(Boolean).map((p) => p.split("=") as [string, string])) };
}

export interface MetricFilter { text: string; family: string; dims: Record<string, string[]> }
export const emptyFilter: MetricFilter = { text: "", family: "", dims: {} };

export function applyFilter(_keys: string[], f: MetricFilter): (k: string) => boolean {
  return (k: string): boolean => {
    if (f.text && !k.toLowerCase().includes(f.text.toLowerCase())) return false;
    const { family, dims } = parseMetric(k);
    if (f.family && family !== f.family) return false;
    for (const [d, vals] of Object.entries(f.dims)) if (vals.length && !vals.includes(dims[d] ?? "")) return false;
    return true;
  };
}

/** Filter a comparison: free text, a metric family, and the values of each dimension the metrics carry (class,
 *  distance, task …) — built from the metrics themselves. */
export function MetricFilterBar({ keys, value, onChange }: { keys: string[]; value: MetricFilter; onChange: (f: MetricFilter) => void }) {
  const families = [...new Set(keys.map((k) => parseMetric(k).family))].sort();
  const dimVals: Record<string, Set<string>> = {};
  keys.forEach((k) => Object.entries(parseMetric(k).dims).forEach(([d, v]) => { (dimVals[d] ??= new Set()).add(v); }));
  const dims = Object.entries(dimVals).filter(([, v]) => v.size > 1 && v.size <= 40).sort(([a], [b]) => a.localeCompare(b));
  const toggle = (d: string, v: string): void => {
    const cur = value.dims[d] ?? [];
    onChange({ ...value, dims: { ...value.dims, [d]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] } });
  };
  const active = !!value.text || !!value.family || Object.values(value.dims).some((v) => v.length);
  return (
    <div className="card metric-filter" data-testid="metric-filter">
      <div className="row wrap" style={{ gap: 8 }}>
        <div className="input-wrap" style={{ width: 260 }}><Search size={13} /><input className="input" aria-label="Filter metrics" placeholder="Filter metrics" value={value.text} onChange={(e) => onChange({ ...value, text: e.target.value })} /></div>
        <select className="input" aria-label="Metric family" style={{ height: 30, maxWidth: 280 }} value={value.family} onChange={(e) => onChange({ ...value, family: e.target.value })}>
          <option value="">All metric families ({families.length})</option>
          {families.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        {active && <button className="btn ghost sm" onClick={() => onChange(emptyFilter)}><X size={12} /> clear filters</button>}
      </div>
      {dims.map(([d, vals]) => (
        <div key={d} className="row wrap" style={{ gap: 4, marginTop: 6 }} data-dim={d}>
          <span className="faint mono" style={{ fontSize: 12, minWidth: 64 }}>{d}</span>
          {[...vals].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).map((v) => (
            <button key={v} className={`filter-chip ${(value.dims[d] ?? []).includes(v) ? "on" : ""}`} aria-pressed={(value.dims[d] ?? []).includes(v)} onClick={() => toggle(d, v)}>{v}</button>
          ))}
        </div>
      ))}
    </div>
  );
}
