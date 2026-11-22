import { Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type FacetConfigDto } from "../../api/client";

/** ``val/AP[class=car,dist=40]`` → family "val/AP", dimensions {class: car, dist: 40}. */
export function parseMetric(k: string): { family: string; dims: Record<string, string> } {
  const i = k.indexOf("[");
  if (i < 0) return { family: k, dims: {} };
  return { family: k.slice(0, i), dims: Object.fromEntries(k.slice(i + 1, -1).split(",").filter(Boolean).map((p) => p.split("=") as [string, string])) };
}

// Dimension names differ between projects: facets map the usual spellings onto the filters everyone uses (class,
// modality, distance). The rules come from the server (defaults/facets.yaml + config/facets.local.yaml, `mf facets`);
// this copy of the defaults applies until it has answered.
let FACETS: FacetConfigDto = {
  order: ["class", "modality", "distance"], hide_dims: [], sources: [], notice: null,
  facets: {
    class: { aliases: ["class", "cls", "category", "label", "classname", "class_name", "obj_class"], values: [], value_aliases: {}, from_name: false, only_listed: false },
    modality: { aliases: ["modality", "sensor", "mod"], values: ["camera", "radar", "lidar", "fusion", "image", "pointcloud", "ultrasonic", "imu", "gps"], value_aliases: {}, from_name: true, only_listed: false },
    distance: { aliases: ["distance", "dist", "range", "dist_bin", "distance_bin", "range_bin"], values: [], value_aliases: {}, from_name: false, only_listed: false },
  },
};
let ALIAS: Record<string, string> = {};
const reindex = (): void => { ALIAS = Object.fromEntries(Object.entries(FACETS.facets).flatMap(([f, s]) => [f, ...s.aliases].map((a) => [a.toLowerCase(), f]))); };
reindex();
const esc = (x: string): string => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Load the facet rules once (components that filter call it; they re-render when the rules arrive). */
export function useFacets(): FacetConfigDto {
  const q = useQuery({ queryKey: ["facets"], queryFn: api.facets, staleTime: 5 * 60_000, retry: false });
  if (q.data && q.data !== FACETS) { FACETS = q.data; reindex(); }
  return FACETS;
}

/** The facets a metric carries: its dimensions (class / distance / modality spelled one way, value aliases applied)
 *  plus a value read from the name when no dimension says it (``module=camera_fpn`` → modality camera). */
export function facetsOf(k: string): { family: string; dims: Record<string, string> } {
  const { family, dims } = parseMetric(k);
  const out: Record<string, string> = {};
  for (const [d, v] of Object.entries(dims)) {
    if (FACETS.hide_dims.includes(d)) continue;
    const f = ALIAS[d.toLowerCase()] ?? d;
    out[f] = FACETS.facets[f]?.value_aliases[v] ?? v;
  }
  const hay = `${Object.values(dims).join(" ")} ${family}`.toLowerCase();
  for (const [f, spec] of Object.entries(FACETS.facets)) {
    if (out[f] || !spec.from_name) continue;
    const v = [...spec.values, ...Object.keys(spec.value_aliases)].find((x) => new RegExp(`(^|[^a-z])${esc(x.toLowerCase())}`).test(hay));
    if (v) out[f] = spec.value_aliases[v] ?? v;
  }
  return { family, dims: out };
}

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
  const cfg = useFacets();
  const FACET_ORDER = cfg.order;
  const rank = (d: string): number => { const i = FACET_ORDER.indexOf(d); return i < 0 ? 99 : i; };
  // values: the configured order first, then the rest as found; only_listed keeps just the configured ones
  const valuesOf = (d: string, vs: Set<string>): string[] => {
    const spec = cfg.facets[d];
    const listed = (spec?.values ?? []).filter((v) => vs.has(v));
    const rest = spec?.only_listed ? [] : [...vs].filter((v) => !listed.includes(v)).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    return [...listed, ...rest];
  };
  const dims = Object.entries(dimVals).map(([d, v]) => [d, valuesOf(d, v)] as [string, string[]])
    .filter(([d, v]) => (v.length > 1 || (FACET_ORDER.includes(d) && v.length > 0)) && v.length <= 40).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
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
          {vals.map((v) => (
            <button key={v} className={`filter-chip ${(value.dims[d] ?? []).includes(v) ? "on" : ""}`} aria-pressed={(value.dims[d] ?? []).includes(v)} onClick={() => toggle(d, v)}>{v}</button>
          ))}
        </div>
      ))}
    </div>
  );
}
