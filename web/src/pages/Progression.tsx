import { useQueries, useQuery } from "@tanstack/react-query";
import { Bot, ChevronDown, Info, Plus, Radio, Search, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, refStr } from "../api/client";
import type { MetricSeriesDto, Run } from "../api/types";
import { useApp } from "../components/AppState";
import { ExplainableRunsChart } from "../components/ChartInsight";
import { PALETTE, type ChartSeries } from "../components/Chart";
import { Card, Empty, Loading, Seg, StatusPill, Switch } from "../components/ui";
import { fmtAgo, fmtNum, fmtValue } from "../lib/format";
import { useDebounced, useLocalStorage } from "../lib/hooks";

type XAxis = "step" | "epoch" | "hours";
const MAX_RUNS = 30;
const DEFAULT_METRICS = ["train/loss", "val/loss", "val/NDS", "val/mAP"];
const EPOCH_KEYS = ["train/epoch", "epoch"];
const lowerIsBetter = (k: string): boolean => /loss|error|err\b|ece|nll|mate|mave|amotp|ids|perplexity/i.test(k);
const short = (r: string): string => r.split(":").slice(1).join(":") || r;

/** Study how loss and metrics progress across many runs (e.g. jobs launched by different people). */
export function ProgressionPage() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const { workspace } = useApp();
  const runs = useMemo(() => (sp.get("runs") ?? "").split(",").filter(Boolean), [sp]);
  const metricsParam = sp.get("metrics");
  const xAxis = (sp.get("x") as XAxis) ?? "step";
  const colorBy = sp.get("color") ?? "run";
  const [smoothing, setSmoothing] = useLocalStorage("mf.prog.smoothing", 0.6);
  const [logY, setLogY] = useLocalStorage("mf.prog.log", false);
  const [cols, setCols] = useLocalStorage<"1" | "2" | "3">("mf.prog.cols", "2");
  const [live, setLive] = useState(true);
  const [pickerQ, setPickerQ] = useState("");
  const [metricFilter, setMetricFilter] = useState("");
  const [metricsOpen, setMetricsOpen] = useState(false);
  const dq = useDebounced(pickerQ, 150);

  const set = (patch: Record<string, string | null>): void => {
    const n = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v === null || v === "" ? n.delete(k) : n.set(k, v)));
    setSp(n, { replace: true });
  };
  const setRuns = (r: string[]): void => set({ runs: [...new Set(r)].slice(0, MAX_RUNS).join(",") });

  const candidates = useQuery({ queryKey: ["runs", "prog-picker", dq, workspace], queryFn: () => api.search(dq, 50, 0, workspace) });
  const info = useQuery({ queryKey: ["run-info", runs], queryFn: () => api.runInfo(runs), enabled: runs.length > 0 });
  const anyRunning = (info.data ?? []).some((r) => r.status === "running" || r.status === "queued");
  const keys = useQuery({ queryKey: ["metric-keys", runs], queryFn: () => api.metricKeys(runs), enabled: runs.length > 0, refetchInterval: live && anyRunning ? 30_000 : false });
  const allKeys = useMemo(() => Object.keys(keys.data ?? {}).filter((k) => !k.includes("[")), [keys.data]);
  const metrics = useMemo(() => {
    if (metricsParam) return metricsParam.split(",").filter(Boolean);
    const d = DEFAULT_METRICS.filter((k) => allKeys.includes(k));
    return d.length ? d : allKeys.slice(0, 2);
  }, [metricsParam, allKeys]);
  const epochKey = EPOCH_KEYS.find((k) => allKeys.includes(k));
  const fetchKeys = useMemo(() => [...new Set([...metrics, ...(xAxis === "epoch" && epochKey ? [epochKey] : [])])], [metrics, xAxis, epochKey]);

  const seriesQ = useQueries({
    queries: runs.map((r) => ({
      queryKey: ["series", "prog", r, fetchKeys],
      queryFn: (): Promise<MetricSeriesDto[]> => api.metrics(r, fetchKeys, 1500),
      enabled: fetchKeys.length > 0,
      refetchInterval: (live && info.data?.some((x) => refStr(x.ref) === r && (x.status === "running" || x.status === "queued")) ? 15_000 : false) as number | false,
    })),
  });
  const needCfg = colorBy.startsWith("cfg:") || colorBy === "cfg";
  const cfgQ = useQueries({ queries: runs.map((r) => ({ queryKey: ["config", r], queryFn: () => api.flatConfig(r), enabled: runs.length > 1 })) });
  const cfgs = cfgQ.map((q) => q.data);
  const differingKeys = useMemo(() => {
    if (cfgs.some((c) => !c) || runs.length < 2) return [];
    const all = new Set(cfgs.flatMap((c) => Object.keys(c!)));
    return [...all].filter((k) => !/output_dir|run_id|run_name|timestamp|work_dir|checkpoint_dir|log_dir/i.test(k))
      .filter((k) => new Set(cfgs.map((c) => fmtValue(c![k]))).size > 1).sort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfgs.map((c) => (c ? 1 : 0)).join(), runs.join()]);

  const runById = useMemo(() => Object.fromEntries((info.data ?? []).map((r) => [refStr(r.ref), r])), [info.data]);
  const groupOf = (r: string, i: number): string => {
    const run = runById[r];
    if (colorBy === "user") return run?.user ?? "unknown";
    if (colorBy === "experiment") return run?.experiment ?? "—";
    if (colorBy === "status") return run?.status ?? "—";
    if (colorBy.startsWith("cfg:")) { const c = cfgs[i]; return c ? fmtValue(c[colorBy.slice(4)]) : "…"; }
    return short(r);
  };
  const groups = runs.map(groupOf);
  const groupColor = useMemo(() => {
    const uniq = [...new Set(groups)];
    return Object.fromEntries(uniq.map((g, i) => [g, PALETTE[i % PALETTE.length]!]));
  }, [groups.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  const xFor = (s: MetricSeriesDto, all: MetricSeriesDto[]): number[] | null => {
    if (xAxis === "step") return s.steps;
    if (xAxis === "hours") {
      if (!s.timestamps) return null;
      const t0 = Math.min(...all.flatMap((x) => (x.timestamps?.length ? [x.timestamps[0]!] : [])));
      return s.timestamps.map((t) => (t - t0) / 3600);
    }
    const ep = all.find((x) => x.key === epochKey);
    if (!ep) return null;
    return s.steps.map((st) => interp(st, ep.steps, ep.values));
  };

  const chartFor = (metric: string): { series: ChartSeries[]; missing: string[] } => {
    const out: ChartSeries[] = [];
    const missing: string[] = [];
    runs.forEach((r, i) => {
      const all = seriesQ[i]?.data ?? [];
      const s = all.find((x) => x.key === metric);
      const x = s ? xFor(s, all) : null;
      if (!s || !x) { if (seriesQ[i]?.data) missing.push(short(r)); return; }
      const g = groups[i]!;
      out.push({ name: colorBy === "run" ? short(r) : `${short(r)} · ${g}`, x, y: s.values, color: groupColor[g] });
    });
    return { series: out, missing };
  };

  const loading = seriesQ.some((q) => q.isLoading);
  const tableMetric = metrics[0];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Progression</h1>
          <div className="sub">Overlay loss and metric curves across runs — e.g. everyone's jobs in a workspace — and see how they evolve.</div>
        </div>
        <div className="actions">
          {runs.length >= 2 && <button className="btn" onClick={() => nav(`/compare?baseline=${encodeURIComponent(runs[0]!)}&candidate=${encodeURIComponent(runs[1]!)}`)}>Compare first two</button>}
          {runs.length >= 1 && <button className="btn primary" title="Ask about all selected runs (first = reference, up to 9)" onClick={() => nav(`/ai?run=${encodeURIComponent(runs[0]!)}${runs.length > 1 ? `&others=${encodeURIComponent(runs.slice(1, 9).join(","))}` : ""}`)}><Bot size={14} /> Ask AI about these runs</button>}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "300px minmax(0,1fr)", gap: 14, alignItems: "start" }}>
        <div className="col" style={{ gap: 14 }}>
          <Card title="Runs" sub={`${runs.length}/${MAX_RUNS}`} right={runs.length > 0 && <button className="btn ghost sm" onClick={() => setRuns([])}><Trash2 size={12} /> Clear</button>} flush>
            {runs.length === 0 ? <div className="faint" style={{ padding: "12px 16px" }}>Add runs from the search below, or select runs in the Runs table and press “Plot progression”.</div> : (
              <div style={{ maxHeight: 300, overflow: "auto" }}>
                {runs.map((r, i) => {
                  const run = runById[r];
                  return (
                    <div key={r} className="row" style={{ padding: "7px 12px", borderBottom: "1px solid var(--line)" }}>
                      <span className="swatch" style={{ background: groupColor[groups[i]!] }} />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <Link to={`/runs/${encodeURIComponent(r)}?tab=metrics`} className="ellipsis" style={{ display: "block", fontWeight: 500 }}>{short(r)}</Link>
                        <div className="faint ellipsis" style={{ fontSize: 11.5 }}>{r.split(":")[0]} · {run?.user ?? "…"} · {fmtAgo(run?.created_at ?? null)}{colorBy !== "run" && colorBy !== "user" ? ` · ${groups[i]}` : ""}</div>
                      </div>
                      {run && <StatusPill status={run.status} />}
                      <button className="btn ghost sm icon" title="Remove" onClick={() => setRuns(runs.filter((x) => x !== r))}><X size={12} /></button>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
          <Card title="Add runs" sub={workspace ? `in ${workspace}` : "all workspaces"} flush>
            <div style={{ padding: 8 }}>
              <div className="input-wrap"><Search size={13} /><input className="input" placeholder="user:alex since:7d, or any text" value={pickerQ} onChange={(e) => setPickerQ(e.target.value)} /></div>
            </div>
            <div className="row" style={{ padding: "0 8px 8px" }}>
              <span className="faint" style={{ fontSize: 12 }}>{candidates.data?.total ?? 0} matches</span>
              <span className="spacer" />
              <button className="btn sm" disabled={!candidates.data?.runs.length} onClick={() => setRuns([...runs, ...(candidates.data?.runs ?? []).map((r) => refStr(r.ref))])}>
                <Plus size={12} /> Add all{(candidates.data?.total ?? 0) > MAX_RUNS ? ` (first ${MAX_RUNS})` : ""}
              </button>
            </div>
            <div style={{ maxHeight: 360, overflow: "auto", borderTop: "1px solid var(--line)" }}>
              {(candidates.data?.runs ?? []).map((r: Run) => {
                const id = refStr(r.ref);
                const on = runs.includes(id);
                return (
                  <div key={id} className="palette-item" style={{ borderRadius: 0 }} onClick={() => setRuns(on ? runs.filter((x) => x !== id) : [...runs, id])}>
                    <input type="checkbox" readOnly checked={on} style={{ accentColor: "var(--accent)" }} />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="ellipsis" style={{ color: "var(--text)" }}>{r.name}</div>
                      <div className="faint ellipsis" style={{ fontSize: 11.5 }}>{workspace ? "" : `${r.ref.backend} · `}{r.user ?? "—"} · {r.experiment ?? "—"} · {fmtAgo(r.created_at)}</div>
                    </div>
                    <StatusPill status={r.status} />
                  </div>
                );
              })}
            </div>
          </Card>
        </div>

        <div className="col" style={{ gap: 14 }}>
          {runs.length === 0 ? <Card><Empty title="No runs selected">Pick runs on the left. Tip: filter by <code>user:</code>, <code>exp:</code> or <code>since:7d</code> and press “Add all”.</Empty></Card> : (
            <>
              <div className="card row wrap" style={{ padding: "8px 12px", gap: 14 }}>
                <div style={{ position: "relative" }}>
                  <button className="btn sm" onClick={() => setMetricsOpen(!metricsOpen)}>Metrics ({metrics.length}) <ChevronDown size={12} /></button>
                  {metricsOpen && (
                    <div className="palette" style={{ position: "absolute", top: 32, left: 0, width: 320, zIndex: 30 }}>
                      <div style={{ padding: 8 }}><input className="input" style={{ width: "100%" }} autoFocus placeholder="Filter metrics" value={metricFilter} onChange={(e) => setMetricFilter(e.target.value)} /></div>
                      <div className="palette-list" style={{ maxHeight: 340 }}>
                        {allKeys.filter((k) => k.toLowerCase().includes(metricFilter.toLowerCase())).map((k) => (
                          <label key={k} className="metric-row">
                            <input type="checkbox" checked={metrics.includes(k)} onChange={() => set({ metrics: (metrics.includes(k) ? metrics.filter((m) => m !== k) : [...metrics, k]).join(",") })} />
                            <span className="mono ellipsis" style={{ flex: 1 }}>{k}</span>
                            <span className="faint" style={{ fontSize: 11 }}>{keys.data?.[k]}/{runs.length}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <span className="row muted">X <Seg<XAxis> options={[{ id: "step", label: "Step" }, { id: "epoch", label: "Epoch" }, { id: "hours", label: "Hours since start" }]} value={xAxis} onChange={(v) => set({ x: v === "step" ? null : v })} /></span>
                <span className="row muted">Color by
                  <select className="select" style={{ height: 28 }} value={colorBy} onChange={(e) => set({ color: e.target.value === "run" ? null : e.target.value })}>
                    <option value="run">run</option><option value="user">user</option><option value="experiment">experiment</option><option value="status">status</option>
                    {differingKeys.length > 0 && <optgroup label="config values that differ">{differingKeys.map((k) => <option key={k} value={`cfg:${k}`}>{k}</option>)}</optgroup>}
                  </select>
                </span>
                <span className="row muted">Smoothing <input className="range" type="range" min={0} max={0.98} step={0.02} value={smoothing} onChange={(e) => setSmoothing(Number(e.target.value))} /><span className="mono" style={{ width: 30 }}>{smoothing.toFixed(2)}</span></span>
                <span className="row muted">Log Y <Switch on={logY} onChange={setLogY} /></span>
                {anyRunning && <span className="row muted" title="Refresh running jobs every 15 s"><Radio size={13} color={live ? "var(--good)" : undefined} /> Live <Switch on={live} onChange={setLive} /></span>}
                <span className="spacer" />
                <Seg options={[{ id: "1", label: "1" }, { id: "2", label: "2" }, { id: "3", label: "3" }]} value={cols} onChange={setCols} />
              </div>
              {xAxis === "epoch" && !epochKey && <div className="note warn"><Info size={14} style={{ marginTop: 2 }} />No <code>epoch</code> metric is logged for these runs, so steps can't be converted to epochs. Log <code>train/epoch</code> to enable this axis.</div>}
              {needCfg && cfgs.some((c) => !c) && <Loading label="Loading configs…" />}
              {loading ? <Loading label="Loading series…" /> : (
                <div className="grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0,1fr))` }}>
                  {metrics.map((m) => {
                    const { series, missing } = chartFor(m);
                    return (
                      <div key={m} className="card">
                        <ExplainableRunsChart runs={runs} metric={m} series={series} smoothing={m.startsWith("train/") ? smoothing : 0} showRaw={false} logY={logY}
                          syncKey="progression" height={cols === "1" ? 340 : 240} xLabel={xAxis === "hours" ? "hours" : xAxis} />
                        {missing.length > 0 && <div className="faint" style={{ fontSize: 11.5, padding: "0 12px 8px" }}>Not logged by: {missing.join(", ")}</div>}
                      </div>
                    );
                  })}
                </div>
              )}
              {tableMetric && !loading && <SummaryTable metric={tableMetric} runs={runs} runById={runById} data={seriesQ.map((q) => q.data)} groups={groups} groupColor={groupColor} colorBy={colorBy} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function SummaryTable({ metric, runs, runById, data, groups, groupColor, colorBy }: {
  metric: string; runs: string[]; runById: Record<string, Run>; data: (MetricSeriesDto[] | undefined)[];
  groups: string[]; groupColor: Record<string, string>; colorBy: string;
}) {
  const lower = lowerIsBetter(metric);
  const rows = runs.map((r, i) => {
    const s = data[i]?.find((x) => x.key === metric);
    const pts = s ? s.steps.map((st, j) => [st, s.values[j]] as const).filter((p): p is readonly [number, number] => p[1] !== null && Number.isFinite(p[1])) : [];
    const best = pts.length ? pts.reduce((a, b) => (lower ? (b[1] < a[1] ? b : a) : b[1] > a[1] ? b : a)) : null;
    const last = pts[pts.length - 1] ?? null;
    const lastTs = s?.timestamps?.[s.timestamps.length - 1];
    return { r, run: runById[r], best, last, n: pts.length, lastTs, g: groups[i]! };
  });
  const ranked = [...rows].filter((x) => x.last).sort((a, b) => (lower ? a.last![1] - b.last![1] : b.last![1] - a.last![1]));
  const rank = Object.fromEntries(ranked.map((x, i) => [x.r, i + 1]));
  return (
    <Card title={<span className="mono">{metric}</span>} sub={`per-run summary · ${lower ? "lower" : "higher"} is better`} flush>
      <div className="table-wrap">
        <table className="t compact">
          <thead><tr><th>#</th><th>Run</th><th>User</th><th>Status</th>{colorBy !== "run" && <th>{colorBy.replace("cfg:", "")}</th>}<th className="r">Final</th><th className="r">Best</th><th className="r">@ step</th><th className="r">Last step</th><th className="r">Points</th><th className="r">Last logged</th></tr></thead>
          <tbody>
            {rows.sort((a, b) => (rank[a.r] ?? 999) - (rank[b.r] ?? 999)).map((x) => (
              <tr key={x.r}>
                <td className="faint num">{rank[x.r] ?? "—"}</td>
                <td><span className="row"><span className="swatch" style={{ background: groupColor[x.g] }} /><Link to={`/runs/${encodeURIComponent(x.r)}`} style={{ fontWeight: 500 }}>{short(x.r)}</Link></span></td>
                <td className="muted">{x.run?.user ?? "—"}</td>
                <td>{x.run && <StatusPill status={x.run.status} />}</td>
                {colorBy !== "run" && <td className="mono">{x.g}</td>}
                <td className="r num">{fmtNum(x.last?.[1])}</td>
                <td className="r num">{fmtNum(x.best?.[1])}</td>
                <td className="r num faint">{x.best ? fmtNum(x.best[0]) : "—"}</td>
                <td className="r num faint">{x.last ? fmtNum(x.last[0]) : "—"}</td>
                <td className="r num faint">{x.n || "not logged"}</td>
                <td className="r faint">{x.lastTs ? fmtAgo(new Date(x.lastTs * 1000).toISOString()) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function interp(x: number, xs: number[], ys: (number | null)[]): number {
  const pts = xs.map((v, i) => [v, ys[i]] as const).filter((p): p is readonly [number, number] => p[1] !== null);
  if (!pts.length) return x;
  if (x <= pts[0]![0]) return pts[0]![1];
  for (let i = 1; i < pts.length; i++) {
    const [x1, y1] = pts[i]!;
    const [x0, y0] = pts[i - 1]!;
    if (x <= x1) return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0 || 1);
  }
  return pts[pts.length - 1]![1];
}
