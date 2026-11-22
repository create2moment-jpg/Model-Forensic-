import { Download, Image as ImageIcon, Maximize2, Minimize2, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";
import { createPortal } from "react-dom";
import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

export const PALETTE = ["#7c6cff", "#3ccf8e", "#ff8a4c", "#4fa9ff", "#f472b6", "#f5b84b", "#22d3ee", "#a3e635", "#e879f9", "#94a3b8"];

export interface ChartSeries {
  name: string;
  x: number[];
  y: (number | null)[];
  color?: string;
  dash?: boolean;
  width?: number;
}
export interface ChartMarker { x: number; label?: string; color?: string }
export interface HLine { y: number; label?: string; color?: string }

export function ema(values: (number | null)[], alpha: number): (number | null)[] {
  if (alpha >= 1) return values;
  let acc = 0;
  let w = 0;
  return values.map((v) => {
    if (v === null || !Number.isFinite(v)) return null;
    acc = (1 - alpha) * acc + alpha * v;
    w = (1 - alpha) * w + alpha;
    return acc / w;
  });
}

const cssVar = (name: string): string => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";

interface Props {
  title?: string;
  series: ChartSeries[];
  height?: number;
  logY?: boolean;
  smoothing?: number; // 0..0.99 TensorBoard-style weight
  showRaw?: boolean;
  markers?: ChartMarker[];
  hlines?: HLine[];
  syncKey?: string;
  xLabel?: string;
  onExpand?: () => void;
  tools?: React.ReactNode;
  badge?: React.ReactNode;
}

export function Chart({ title, series, height = 240, logY, smoothing = 0, showRaw = true, markers = [], hlines = [], syncKey, xLabel = "step", onExpand, tools, badge }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const [width, setWidth] = useState(0);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme);
  // Full screen (an overlay, Esc closes) and zoom (buttons, drag, Ctrl/⌘+wheel; plain wheel in full screen).
  const [full, setFull] = useState(false);
  const [vh, setVh] = useState(window.innerHeight);
  const xRange = useRef<[number, number] | null>(null); // the user's zoom, kept when the plot is rebuilt
  const creating = useRef(false);
  const h = full ? Math.max(300, vh - 150) : height;

  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") setFull(false); };
    const onResize = (): void => setVh(window.innerHeight);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("resize", onResize); };
  }, [full]);

  useEffect(() => {
    const mo = new MutationObserver(() => setTheme(document.documentElement.dataset.theme));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);

  useEffect(() => {
    if (!host.current) return;
    const ro = new ResizeObserver((e) => setWidth(Math.floor(e[0]?.contentRect.width ?? 0)));
    ro.observe(host.current);
    return () => ro.disconnect();
  }, [full]);

  const { data, opts } = useMemo(() => {
    const alpha = 1 - smoothing;
    const tables: uPlot.AlignedData[] = [];
    const sOpts: uPlot.Series[] = [{ label: xLabel }];
    series.forEach((s, i) => {
      const color = s.color ?? PALETTE[i % PALETTE.length]!;
      const ys = logY ? s.y.map((v) => (v !== null && v > 0 ? v : null)) : s.y;
      if (smoothing > 0 && showRaw) {
        tables.push([s.x, ys] as uPlot.AlignedData);
        sOpts.push({ label: `${s.name} (raw)`, stroke: color + "40", width: 1, spanGaps: true, show: true, value: (_u, v) => fmt(v) });
      }
      tables.push([s.x, smoothing > 0 ? ema(ys, alpha) : ys] as uPlot.AlignedData);
      sOpts.push({ label: s.name, stroke: color, width: s.width ?? 1.6, dash: s.dash ? [5, 4] : undefined, spanGaps: true, points: { show: s.x.length < 40, size: 4 }, value: (_u, v) => fmt(v) });
    });
    const joined = tables.length ? uPlot.join(tables) : ([[]] as unknown as uPlot.AlignedData);
    const axis = cssVar("--chart-axis");
    const grid = cssVar("--chart-grid");
    const o: uPlot.Options = {
      width: Math.max(width, 200),
      height: h,
      series: sOpts,
      scales: { x: { time: false }, y: { distr: logY ? 3 : 1 } },
      axes: [
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, font: "11px Inter, system-ui", size: 34 },
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, font: "11px Inter, system-ui", size: 56, values: (_u, vals) => vals.map((v) => fmt(v)) },
      ],
      cursor: { sync: syncKey ? { key: syncKey } : undefined, drag: { x: true, y: false, setScale: true }, points: { size: 6 } },
      legend: { show: true, live: true },
      hooks: {
        setScale: [
          (u, key) => {
            if (key !== "x") return;
            const sc = u.scales.x ?? {};
            if (sc.min !== undefined && sc.max !== undefined) host.current?.setAttribute("data-xrange", `${sc.min}:${sc.max}`);
            if (creating.current) return;
            const { min, max } = sc;
            const ext = extent(u);
            xRange.current = min === undefined || max === undefined || !ext || (min <= ext[0] && max >= ext[1]) ? null : [min, max];
          },
        ],
        draw: [
          (u) => {
            const ctx = u.ctx;
            ctx.save();
            ctx.font = `${10 * devicePixelRatio}px Inter, system-ui`;
            ctx.textAlign = "left"; // uPlot leaves "right" from the y-axis labels
            ctx.textBaseline = "alphabetic";
            const rowsEnd: number[] = []; // right edge of the last label per text row: labels never overlap
            const lineH = 13 * devicePixelRatio;
            for (const m of [...markers].sort((a, b) => a.x - b.x)) {
              const px = u.valToPos(m.x, "x", true);
              if (px < u.bbox.left || px > u.bbox.left + u.bbox.width) continue;
              ctx.strokeStyle = m.color ?? "#f5b84b";
              ctx.setLineDash([4 * devicePixelRatio, 4 * devicePixelRatio]);
              ctx.beginPath(); ctx.moveTo(px, u.bbox.top); ctx.lineTo(px, u.bbox.top + u.bbox.height); ctx.stroke();
              if (m.label) {
                const w = ctx.measureText(m.label).width;
                const right = u.bbox.left + u.bbox.width;
                const x = px + 4 + w > right ? Math.max(u.bbox.left + 2, px - 4 - w) : px + 4;
                let row = rowsEnd.findIndex((end) => end < x);
                if (row < 0) row = rowsEnd.length;
                rowsEnd[row] = x + w + 6;
                ctx.fillStyle = m.color ?? "#f5b84b";
                ctx.fillText(m.label, x, u.bbox.top + lineH * (row + 1));
              }
            }
            for (const h of hlines) {
              const py = u.valToPos(h.y, "y", true);
              if (py < u.bbox.top || py > u.bbox.top + u.bbox.height) continue;
              ctx.strokeStyle = h.color ?? "#94a3b8";
              ctx.setLineDash([6 * devicePixelRatio, 4 * devicePixelRatio]);
              ctx.beginPath(); ctx.moveTo(u.bbox.left, py); ctx.lineTo(u.bbox.left + u.bbox.width, py); ctx.stroke();
              if (h.label) { ctx.fillStyle = h.color ?? "#94a3b8"; ctx.fillText(h.label, u.bbox.left + 6, py - 4 * devicePixelRatio); }
            }
            ctx.restore();
          },
        ],
      },
    };
    return { data: joined, opts: o };
  }, [series, logY, smoothing, showRaw, markers, hlines, syncKey, h, width, xLabel, theme]);

  useEffect(() => {
    if (!host.current || width === 0) return;
    plot.current?.destroy();
    creating.current = true;
    const u = new uPlot(opts, data, host.current);
    plot.current = u;
    if (xRange.current) u.setScale("x", { min: xRange.current[0], max: xRange.current[1] });
    creating.current = false;
    const onWheel = (e: WheelEvent): void => {
      if (!(full || e.ctrlKey || e.metaKey)) return; // plain wheel scrolls the page outside full screen
      e.preventDefault();
      const rect = u.over.getBoundingClientRect();
      zoomAt(u, e.deltaY < 0 ? 0.8 : 1.25, u.posToVal(e.clientX - rect.left, "x"));
    };
    u.over.addEventListener("wheel", onWheel, { passive: false });
    return () => { u.over.removeEventListener("wheel", onWheel); u.destroy(); plot.current = null; };
  }, [opts, data, width, full]);

  const zoom = (factor: number): void => { if (plot.current) zoomAt(plot.current, factor); };
  const resetZoom = (): void => {
    const u = plot.current;
    const ext = u && extent(u);
    xRange.current = null;
    if (u && ext) u.setScale("x", { min: ext[0], max: ext[1] });
  };

  const exportPng = (): void => {
    const canvas = host.current?.querySelector("canvas");
    if (!canvas) return;
    const a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = `${(title ?? "chart").replace(/[^\w.-]+/g, "_")}.png`;
    a.click();
  };
  const exportCsv = (): void => {
    const rows = ["series,x,y", ...series.flatMap((s) => s.x.map((x, i) => `${JSON.stringify(s.name)},${x},${s.y[i] ?? ""}`))];
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" }));
    a.download = `${(title ?? "chart").replace(/[^\w.-]+/g, "_")}.csv`;
    a.click();
  };

  const card = (
    <div className={`chart-card ${full ? "full" : ""}`} data-testid={full ? "chart-full" : undefined}>
      {(
        <div className="chart-title">
          {title ? <b className="ellipsis mono">{title}</b> : <span className="spacer" />}
          {badge}
          <div className="tools">
            {tools}
            <button className="btn ghost sm icon" title="Zoom in (or drag across the chart; Ctrl/⌘ + wheel)" aria-label="Zoom in" onClick={() => zoom(0.5)}><ZoomIn size={13} /></button>
            <button className="btn ghost sm icon" title="Zoom out" aria-label="Zoom out" onClick={() => zoom(2)}><ZoomOut size={13} /></button>
            <button className="btn ghost sm icon" title="Reset zoom (or double-click the chart)" aria-label="Reset zoom" onClick={resetZoom}><RotateCcw size={13} /></button>
            <button className="btn ghost sm icon" title={full ? "Exit full screen (Esc)" : "Full screen"} aria-label={full ? "Exit full screen" : "Full screen"} onClick={() => setFull(!full)}>{full ? <Minimize2 size={13} /> : <Maximize2 size={13} />}</button>
            <button className="btn ghost sm icon" title="Export PNG" onClick={exportPng}><ImageIcon size={13} /></button>
            <button className="btn ghost sm icon" title="Export CSV" onClick={exportCsv}><Download size={13} /></button>
            {onExpand && <button className="btn ghost sm icon" title="Expand" onClick={onExpand}><Maximize2 size={13} /></button>}
          </div>
        </div>
      )}
      <div className="chart" ref={host} style={{ padding: "6px 8px 8px", minHeight: h + 30 }} />
    </div>
  );
  if (!full) return card;
  return (
    <>
      <div className="chart-card" style={{ minHeight: height + 60 }} />
      {createPortal(<><div className="chart-backdrop" onClick={() => setFull(false)} />{card}</>, document.body)}
    </>
  );
}

/** The x extent of all data in the plot. */
function extent(u: uPlot): [number, number] | null {
  const xs = (u.data[0] ?? []) as number[];
  if (!xs.length) return null;
  return [xs[0]!, xs[xs.length - 1]!];
}

/** Zoom the x axis by ``factor`` (< 1 in, > 1 out) around ``at`` (default: the middle of the visible range). */
function zoomAt(u: uPlot, factor: number, at?: number): void {
  const { min, max } = u.scales.x ?? {};
  const ext = extent(u);
  if (min === undefined || max === undefined || !ext) return;
  const c = at ?? (min + max) / 2;
  let lo = c - (c - min) * factor;
  let hi = c + (max - c) * factor;
  if (hi - lo >= ext[1] - ext[0]) [lo, hi] = ext;
  else if (lo < ext[0]) [lo, hi] = [ext[0], ext[0] + (hi - lo)];
  else if (hi > ext[1]) [lo, hi] = [ext[1] - (hi - lo), ext[1]];
  u.setScale("x", { min: lo, max: hi });
}

function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(2);
  return Number(v.toPrecision(4)).toString();
}
