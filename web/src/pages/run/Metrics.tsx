import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../../api/client";
import type { MetricSeriesDto } from "../../api/types";
import { PALETTE, type ChartSeries } from "../../components/Chart";
import { ExplainableChart } from "../../components/ChartInsight";
import { Card, Empty, Loading, Seg, Switch } from "../../components/ui";
import { fmtNum } from "../../lib/format";
import { useLocalStorage } from "../../lib/hooks";

const parse = (k: string): { base: string; dims: string | null } => {
  const i = k.indexOf("[");
  return i < 0 ? { base: k, dims: null } : { base: k.slice(0, i), dims: k.slice(i + 1, -1) };
};
const DEFAULT_PICKS = ["train/loss", "val/loss", "val/NDS", "val/mAP", "val/AMOTA", "train/lr"];

/** Multi-metric explorer: per-metric charts, smoothing, log scale, baseline overlay, synced cursors, slice breakdowns. */
interface Entry { key: string; metric: string; stage: string | null; label: string }

/** Multi-metric explorer. Pipelines: every metric is listed as ``stage / metric`` (the stage that logged it), and
 *  stages can be switched on or off (remembered by stage name, e.g. hide train_pretrain everywhere). */
export function MetricsExplorer({ runId, keys, baseline, extraRuns = [] }: { runId: string; keys: string[]; baseline: string | null; extraRuns?: string[] }) {
  const cat = useQuery({ queryKey: ["metric-catalog", runId], queryFn: () => api.metricCatalog(runId), staleTime: 60_000 });
  const entries: Entry[] = useMemo(() => cat.data?.entries ?? keys.map((k) => ({ key: k, metric: k, stage: null, label: k })), [cat.data, keys]);
  const stages = cat.data?.stages ?? [];
  const [hiddenStages, setHiddenStages] = useLocalStorage<string[]>("mf.metrics.hiddenStages", []);
  const visible = useMemo(() => entries.filter((e) => !e.stage || !hiddenStages.includes(e.stage)), [entries, hiddenStages]);
  const byKey = useMemo(() => new Map(entries.map((e) => [e.key, e])), [entries]);
  const scalarKeys = visible.filter((e) => !e.metric.includes("[")).map((e) => e.key);
  const sliceKeys = visible.filter((e) => e.metric.includes("[")).map((e) => e.key);
  const [filter, setFilter] = useState("");
  const [pickedRaw, setPicked] = useState<string[] | null>(null);
  const defaults = useMemo(() => {
    const d = visible.filter((e) => DEFAULT_PICKS.includes(e.metric)).map((e) => e.key).slice(0, 6);
    return d.length ? d : scalarKeys.slice(0, 4);
  }, [visible, scalarKeys]);
  const picked = (pickedRaw ?? defaults).filter((k) => scalarKeys.includes(k));
  const toggleStage = (st: string): void => setHiddenStages(hiddenStages.includes(st) ? hiddenStages.filter((x) => x !== st) : [...hiddenStages, st]);
  const [smoothing, setSmoothing] = useLocalStorage("mf.metrics.smoothing", 0.6);
  const [logY, setLogY] = useLocalStorage("mf.metrics.log", false);
  const [cols, setCols] = useLocalStorage<"1" | "2" | "3">("mf.metrics.cols", "2");
  const [overlay, setOverlay] = useState(true);
  const runs = [runId, ...(overlay && baseline && baseline !== runId ? [baseline] : []), ...extraRuns];

  const series = useQuery({
    queryKey: ["series", runs, picked],
    queryFn: async () => Object.fromEntries(await Promise.all(runs.map(async (r) => [r, await api.metrics(r, picked, 2000)] as const))),
    enabled: picked.length > 0,
  });
  const slices = useQuery({
    queryKey: ["series", "slices", runs, sliceKeys.length],
    queryFn: async () => Object.fromEntries(await Promise.all(runs.map(async (r) => [r, await api.metrics(r, sliceKeys, 10)] as const))),
    enabled: sliceKeys.length > 0,
  });

  const groups = useMemo(() => {
    const g = new Map<string, Entry[]>();
    visible.filter((e) => !e.metric.includes("[") && e.label.toLowerCase().includes(filter.toLowerCase())).forEach((e) => {
      const head = `${e.stage ? `${e.stage} / ` : ""}${e.metric.split("/")[0] ?? "other"}`;
      g.set(head, [...(g.get(head) ?? []), e]);
    });
    return [...g.entries()];
  }, [visible, filter]);

  const toggle = (k: string): void => setPicked((p) => { const cur = p ?? defaults; return cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]; });
  const chartsFor = (key: string): ChartSeries[] =>
    runs.flatMap((r, i) => {
      const s = series.data?.[r]?.find((x: MetricSeriesDto) => x.key === key);
      if (!s) return [];
      return [{ name: i === 0 ? r.split(":").pop()! : `${r.split(":").pop()!}${r === baseline ? " (baseline)" : ""}`, x: s.steps, y: s.values, color: PALETTE[i % PALETTE.length], dash: r === baseline }];
    });

  return (
    <div style={{ display: "grid", gridTemplateColumns: "260px minmax(0,1fr)", gap: 14, alignItems: "start" }}>
      <Card title="Metrics" sub={`${picked.length}/${scalarKeys.length}`} flush>
        {stages.length > 0 && (
          <div className="row wrap" style={{ gap: 4, padding: "8px 8px 0" }} data-testid="stage-toggles" title="Show or hide the metrics of a stage (remembered by stage name)">
            {stages.map((st) => (
              <button key={st} className={`filter-chip ${hiddenStages.includes(st) ? "" : "on"}`} aria-pressed={!hiddenStages.includes(st)} onClick={() => toggleStage(st)}>{st}</button>
            ))}
          </div>
        )}
        <div style={{ padding: 8 }}>
          <div className="input-wrap"><Search size={13} /><input className="input" placeholder="Filter metrics" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
        </div>
        <div className="metric-list" style={{ paddingBottom: 8 }}>
          {groups.map(([g, es]) => (
            <div key={g}>
              <div className="palette-group" style={{ textTransform: "none" }}>{g}</div>
              {es.map((e) => (
                <label key={e.key} className="metric-row">
                  <input type="checkbox" checked={picked.includes(e.key)} onChange={() => toggle(e.key)} />
                  <span className="mono ellipsis" title={e.label}>{e.metric.split("/").slice(1).join("/") || e.metric}</span>
                </label>
              ))}
            </div>
          ))}
        </div>
      </Card>
      <div className="col" style={{ gap: 14 }}>
        <div className="card row wrap" style={{ padding: "8px 12px", gap: 16 }}>
          <span className="row muted">Smoothing <input className="range" type="range" min={0} max={0.98} step={0.02} value={smoothing} onChange={(e) => setSmoothing(Number(e.target.value))} /><span className="mono" style={{ width: 32 }}>{smoothing.toFixed(2)}</span></span>
          <span className="row muted">Log Y <Switch on={logY} onChange={setLogY} /></span>
          {baseline && baseline !== runId && <span className="row muted">Overlay baseline <Switch on={overlay} onChange={setOverlay} /><span className="mono faint">{baseline.split(":").pop()}</span></span>}
          <span className="spacer" />
          <Seg options={[{ id: "1", label: "1 col" }, { id: "2", label: "2 col" }, { id: "3", label: "3 col" }]} value={cols} onChange={setCols} />
        </div>
        {picked.length === 0 ? <Card><Empty title="Select metrics on the left" /></Card> : series.isLoading ? <Loading /> : (
          <div className="grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0,1fr))` }}>
            {picked.map((k) => (
              <div key={k} className="card">
                <ExplainableChart runId={runId} metric={k} title={byKey.get(k)?.label ?? k} baseline={baseline} series={chartsFor(k)}
                  nonFinite={series.data?.[runId]?.find((x: MetricSeriesDto) => x.key === k)?.non_finite ?? 0}
                  smoothing={(byKey.get(k)?.metric ?? k).startsWith("train/") ? smoothing : 0} logY={logY} syncKey={`run-${runId}`} height={cols === "1" ? 320 : 220} />
              </div>
            ))}
          </div>
        )}
        {sliceKeys.length > 0 && <SliceBreakdowns data={slices.data} runs={runs} baseline={baseline} />}
      </div>
    </div>
  );
}

function SliceBreakdowns({ data, runs, baseline }: { data: Record<string, MetricSeriesDto[]> | undefined; runs: string[]; baseline: string | null }) {
  if (!data) return <Loading label="Loading slice metrics…" />;
  const byBase = new Map<string, Map<string, Record<string, number>>>();
  runs.forEach((r) => (data[r] ?? []).forEach((s) => {
    const { base, dims } = parse(s.key);
    if (!dims) return;
    const v = [...s.values].reverse().find((x) => x !== null);
    if (v === undefined || v === null) return;
    const m = byBase.get(base) ?? new Map();
    const row = m.get(dims) ?? {};
    row[r] = v;
    m.set(dims, row);
    byBase.set(base, m);
  }));
  return (
    <Card title="Slice breakdowns" sub="final value per dimension slice — where exactly did performance change?">
      <div className="grid g2">
        {[...byBase.entries()].map(([base, rows]) => {
          const max = Math.max(...[...rows.values()].flatMap((r) => Object.values(r)));
          return (
            <div key={base}>
              <div className="sub-h mono" style={{ textTransform: "none" }}>{base}</div>
              <table className="t compact">
                <tbody>
                  {[...rows.entries()].map(([dims, vals]) => {
                    const cand = vals[runs[0]!];
                    const base0 = baseline ? vals[baseline] : undefined;
                    const rel = cand !== undefined && base0 ? (cand - base0) / Math.abs(base0) : null;
                    return (
                      <tr key={dims}>
                        <td className="mono" style={{ width: 130 }}>{dims}</td>
                        <td style={{ width: "45%" }}>
                          {runs.map((r, i) => vals[r] !== undefined && (
                            <div key={r} style={{ height: 6, margin: "2px 0", borderRadius: 3, width: `${(vals[r]! / max) * 100}%`, background: PALETTE[i % PALETTE.length], opacity: r === baseline ? 0.55 : 1 }} />
                          ))}
                        </td>
                        <td className="r num">{fmtNum(cand)}</td>
                        <td className="r num" style={{ color: rel === null ? undefined : rel < -0.02 ? "var(--bad)" : rel > 0.02 ? "var(--good)" : "var(--text-3)" }}>{rel === null ? "" : `${rel > 0 ? "+" : ""}${(rel * 100).toFixed(1)}%`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
