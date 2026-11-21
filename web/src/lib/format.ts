export const fmtNum = (v: number | null | undefined, digits = 4): string => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (!Number.isFinite(v)) return v > 0 ? "∞" : "−∞";
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(2);
  return Number(v.toPrecision(digits)).toString();
};
export const fmtPct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
export const fmtBytes = (b: number | null | undefined): string => {
  if (b === null || b === undefined) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let x = b;
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(x < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
};
export const fmtDuration = (start: string | null, end: string | null): string => {
  if (!start || !end) return "—";
  const s = (new Date(end).getTime() - new Date(start).getTime()) / 1000;
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s / 60).toFixed(0)}m`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
};
export const fmtAgo = (iso: string | null): string => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
};
export const fmtDate = (iso: string | null): string => (iso ? new Date(iso).toLocaleString() : "—");
export const shortSha = (s: string | null | undefined): string => (s ? s.slice(0, 8) : "—");
export const fmtValue = (v: unknown): string => {
  if (v === null || v === undefined) return "∅";
  if (typeof v === "number") return fmtNum(v, 6);
  if (typeof v === "string") return v;
  return JSON.stringify(v);
};
