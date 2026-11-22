import { Search, X } from "lucide-react";
import { useState } from "react";

/** ``val/AP[class=car,dist=40]`` → family "val/AP", dimensions {class: car, dist: 40}. */
export function parseMetric(k: string): { family: string; dims: Record<string, string> } {
  const i = k.indexOf("[");
  if (i < 0) return { family: k, dims: {} };
  return { family: k.slice(0, i), dims: Object.fromEntries(k.slice(i + 1, -1).split(",").filter(Boolean).map((p) => p.split("=") as [string, string])) };
}

// Dimension names differ between projects: map the usual spellings onto three facets everyone filters by.
const ALIAS: Record<string, string> = {
  cls: "class", category: "class", label: "class", classname: "class", class_name: "class", obj_class: "class",
  dist: "distance", range: "distance", dist_bin: "distance", distance_bin: "distance", range_bin: "distance",
  sensor: "modality", modality: "modality", mod: "modality",
};
const MODALITIES = ["camera", "radar", "lidar", "fusion", "image", "pointcloud", "ultrasonic", "imu", "gps"];
/** The facets a metric carries: its dimensions (class / distance / modality spelled one way) plus a modality read
 *  from the name when no dimension says it (``module=camera_fpn`` or ``loss/radar_head`` → modality camera / radar). */
export function facetsOf(k: string): { family: string; dims: Record<string, string> } {
  const { family, dims } = parseMetric(k);
  const out: Record<string, string> = {};
  for (const [d, v] of Object.entries(dims)) out[ALIAS[d.toLowerCase()] ?? d] = v;
  if (!out.modality) {
    const hay = `${Object.values(dims).join(" ")} ${family}`.toLowerCase();
    const m = MODALITIES.find((x) => new RegExp(`(^|[^a-z])${x}`).test(hay));
    if (m) out.modality = m;
  }
  return { family, dims: out };
}
const FACET_ORDER = ["class", "modality", "distance"];

export interface MetricFilter { text: string; family: string; dims: Record<string, string[]> }
export const emptyFilter: MetricFilter = { text: "", family: "", dims: {} };

export function applyFilter(_keys: string[], f: MetricFilter): (k: string) => boolean {
  return (k: string): boolean => {
    if (f.text && !k.toLowerCase().includes(f.text.toLowerCase())) return false;
    const { family, dims } = facetsOf(k);
    if (f.family && family !== f.family) return false;
    for (const [d, vals] of Object.entries(f.dims)) if (vals.length && !vals.includes(dims[d] ?? "")) return false;
    return true;
  };
}

/** Filter a comparison: free text, a metric family, and the values of each dimension the metrics carry (class,
 *  distance, task …) — built from the metrics themselves. */
export function MetricFilterBar({ keys, value, onChange, testId = "metric-filter", compact = false }: { keys: string[]; value: MetricFilter; onChange: (f: MetricFilter) => void; testId?: string; compact?: boolean }) {
  const families = [...new Set(keys.map((k) => parseMetric(k).family))].sort();
  const dimVals: Record<string, Set<string>> = {};
  keys.forEach((k) => Object.entries(facetsOf(k).dims).forEach(([d, v]) => { (dimVals[d] ??= new Set()).add(v); }));
  const rank = (d: string): number => { const i = FACET_ORDER.indexOf(d); return i < 0 ? 99 : i; };
  const dims = Object.entries(dimVals).filter(([d, v]) => (v.size > 1 || FACET_ORDER.includes(d)) && v.size <= 40).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
  const toggle = (d: string, v: string): void => {
    const cur = value.dims[d] ?? [];
    onChange({ ...value, dims: { ...value.dims, [d]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] } });
  };
  const [more, setMore] = useState(false);
  // compact: class / modality / distance (and any dimension in use) first, the others behind a toggle
  const main = compact && !more ? dims.filter(([d]) => FACET_ORDER.includes(d) || (value.dims[d] ?? []).length) : dims;
  const rest = dims.length - main.length;
  const active = !!value.text || !!value.family || Object.values(value.dims).some((v) => v.length);
  return (
    <div className={`card metric-filter ${compact ? "compact" : ""}`} data-testid={testId}>
      <div className="row wrap" style={{ gap: 8 }}>
        <div className="input-wrap" style={{ width: 260 }}><Search size={13} /><input className="input" aria-label="Filter metrics" placeholder="Filter metrics" value={value.text} onChange={(e) => onChange({ ...value, text: e.target.value })} /></div>
        <select className="input" aria-label="Metric family" style={{ height: 30, maxWidth: 280 }} value={value.family} onChange={(e) => onChange({ ...value, family: e.target.value })}>
          <option value="">All families ({families.length})</option>
          {families.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        {compact && (rest > 0 || more) && <button className="btn ghost sm" onClick={() => setMore(!more)}>{more ? "fewer dimensions" : `+ ${rest} more dimensions (${dims.filter(([d]) => !main.some(([m]) => m === d)).map(([d]) => d).slice(0, 4).join(", ")})`}</button>}
        {active && <button className="btn ghost sm" onClick={() => onChange(emptyFilter)}><X size={12} /> clear filters</button>}
      </div>
      {main.map(([d, vals]) => (
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
