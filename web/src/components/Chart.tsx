import { Download, Image as ImageIcon, Maximize2 } from "lucide-react";
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
}

export function Chart({ title, series, height = 240, logY, smoothing = 0, showRaw = true, markers = [], hlines = [], syncKey, xLabel = "step", onExpand }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const [width, setWidth] = useState(0);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme);

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
  }, []);

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
      height,
      series: sOpts,
      scales: { x: { time: false }, y: { distr: logY ? 3 : 1 } },
      axes: [
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, font: "11px Inter, system-ui", size: 34 },
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, font: "11px Inter, system-ui", size: 56, values: (_u, vals) => vals.map((v) => fmt(v)) },
      ],
      cursor: { sync: syncKey ? { key: syncKey } : undefined, drag: { x: true, y: false, setScale: true }, points: { size: 6 } },
      legend: { show: true, live: true },
      hooks: {
        draw: [
          (u) => {
            const ctx = u.ctx;
            ctx.save();
            ctx.font = `${10 * devicePixelRatio}px Inter, system-ui`;
            for (const m of markers) {
              const px = u.valToPos(m.x, "x", true);
              if (px < u.bbox.left || px > u.bbox.left + u.bbox.width) continue;
              ctx.strokeStyle = m.color ?? "#f5b84b";
              ctx.setLineDash([4 * devicePixelRatio, 4 * devicePixelRatio]);
              ctx.beginPath(); ctx.moveTo(px, u.bbox.top); ctx.lineTo(px, u.bbox.top + u.bbox.height); ctx.stroke();
              if (m.label) { ctx.fillStyle = m.color ?? "#f5b84b"; ctx.fillText(m.label, px + 4, u.bbox.top + 12 * devicePixelRatio); }
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
  }, [series, logY, smoothing, showRaw, markers, hlines, syncKey, height, width, xLabel, theme]);

  useEffect(() => {
    if (!host.current || width === 0) return;
    plot.current?.destroy();
    plot.current = new uPlot(opts, data, host.current);
    return () => { plot.current?.destroy(); plot.current = null; };
  }, [opts, data, width]);

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

  return (
    <div className="chart-card">
      {title && (
        <div className="chart-title">
          <b className="ellipsis mono">{title}</b>
          <div className="tools">
            <button className="btn ghost sm icon" title="Export PNG" onClick={exportPng}><ImageIcon size={13} /></button>
            <button className="btn ghost sm icon" title="Export CSV" onClick={exportCsv}><Download size={13} /></button>
            {onExpand && <button className="btn ghost sm icon" title="Expand" onClick={onExpand}><Maximize2 size={13} /></button>}
          </div>
        </div>
      )}
      <div className="chart" ref={host} style={{ padding: "6px 8px 8px", minHeight: height + 30 }} />
    </div>
  );
}

function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(2);
  return Number(v.toPrecision(4)).toString();
}
