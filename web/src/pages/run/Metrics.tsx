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

interface Entry { key: string; metric: string; stage: string | null; label: string }
interface MetricSet { id: string; label: string; keys: string[]; together: boolean }
const isLoss = (e: Entry): boolean => /loss/i.test(e.metric);

/** Where a metric goes in the list and what its row says: ``train/loss`` → train · loss; ``x/y[layer=3]`` → x/y ·
 *  layer=3; ``CpuMonitor.ctx_switches`` (system metrics, no split) → CpuMonitor · ctx_switches; else other. */
function placeOf(e: Entry): { head: string; label: string } {
  const st = e.stage ? `${e.stage} / ` : "";
  const m = e.metric;
  if (m.includes("[")) return { head: `${st}${baseOf(m)}`, label: m.slice(m.indexOf("[") + 1, -1).replace(/,/g, " · ") };
  if (m.includes("/")) return { head: `${st}${m.split("/")[0]}`, label: m.split("/").slice(1).join("/") };
  const dot = m.indexOf(".");
  if (dot > 0) return { head: `${st}${m.slice(0, dot)}`, label: m.slice(dot + 1) };
  return { head: `${st}other`, label: m };
}

/** One searchable, grouped list of metrics with checkboxes (used for the metrics on the left and the losses on the right). */
function MetricList({ title, entries, picked, onToggle, testId }: { title: string; entries: Entry[]; picked: string[]; onToggle: (k: string) => void; testId: string }) {
  const [filter, setFilter] = useState("");
  const groups = useMemo(() => {
    const g = new Map<string, { e: Entry; label: string }[]>();
    entries.filter((e) => e.label.toLowerCase().includes(filter.toLowerCase())).forEach((e) => {
      const { head, label } = placeOf(e);
      g.set(head, [...(g.get(head) ?? []), { e, label }]);
    });
    // a dimension with the same value in every row of a group says nothing per row: show it once in the header
    const out: [string, { e: Entry; label: string }[]][] = [...g.entries()].map(([head, rows]) => {
      const parts = rows.map((r) => r.label.split(" · "));
      const common = rows.length > 1 && parts.every((p) => p.length > 1) ? parts[0]!.filter((x) => x.includes("=") && parts.every((p) => p.includes(x))) : [];
      if (!common.length) return [head, rows];
      return [`${head} · ${common.join(" · ")}`, rows.map((r, i) => ({ ...r, label: parts[i]!.filter((x) => !common.includes(x)).join(" · ") || r.label }))];
    });
    return out.sort(([a, x], [b, y]) => Number(x[0]!.e.metric.includes("[")) - Number(y[0]!.e.metric.includes("[")) || a.localeCompare(b));
  }, [entries, filter]);
  const n = entries.filter((e) => picked.includes(e.key)).length;
  return (
    <Card title={title} sub={`${n} of ${entries.length}`} flush>
      <div style={{ padding: 8 }}>
        <div className="input-wrap"><Search size={13} /><input className="input" aria-label={`Filter ${title.toLowerCase()}`} placeholder={`Filter ${title.toLowerCase()}`} value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
      </div>
      <div className="metric-list" style={{ paddingBottom: 8 }} data-testid={testId}>
        {groups.length === 0 && <div className="faint" style={{ padding: "4px 12px" }}>none</div>}
        {groups.map(([g, es]) => (
          <div key={g}>
            <div className="palette-group trunc" style={{ textTransform: "none" }}>{g}</div>
            {es.map(({ e, label }) => (
              <label key={e.key} className="metric-row">
                <input type="checkbox" checked={picked.includes(e.key)} onChange={() => onToggle(e.key)} />
                <span className="mono trunc" title={e.label}>{label}</span>
              </label>
            ))}
          </div>
        ))}
      </div>
    </Card>
  );
}

const baseOf = (m: string): string => (m.includes("[") ? m.slice(0, m.indexOf("[")) : m);
const dimsOf = (m: string): Record<string, string> => Object.fromEntries((m.includes("[") ? m.slice(m.indexOf("[") + 1, -1) : "").split(",").filter(Boolean).map((p) => p.split("=") as [string, string]));

/** Sets of metrics worth looking at together, from the names the run actually has (no project names in code):
 *  all losses, every loss family (and per term inside it), evaluation, learning rate, learned task weights. */
function buildSets(entries: Entry[]): MetricSet[] {
  const out: MetricSet[] = [];
  const add = (id: string, label: string, keys: string[], together: boolean): void => { if (keys.length > 1 || (keys.length === 1 && id.startsWith("lr"))) out.push({ id, label, keys: keys.slice(0, 12), together }); };
  const plain = entries.filter((e) => !e.metric.includes("["));
  add("losses", `All losses (${plain.filter((e) => /loss/i.test(e.metric)).length})`, plain.filter((e) => /loss/i.test(e.metric)).map((e) => e.key), true);
  add("eval", "Evaluation", plain.filter((e) => /^(val|test)\//.test(e.metric) && !/loss/i.test(e.metric)).map((e) => e.key), false);
  add("lr", "Learning rate", plain.filter((e) => /(^|\/)(lr|learning_rate)$/.test(e.metric)).map((e) => e.key), false);
  const byBase = new Map<string, Entry[]>();
  entries.filter((e) => e.metric.includes("[")).forEach((e) => { const b = `${e.stage ?? ""}|${baseOf(e.metric)}`; byBase.set(b, [...(byBase.get(b) ?? []), e]); });
  for (const [b, es] of byBase) {
    const [stage, base] = b.split("|") as [string, string];
    const name = `${stage ? `${stage} / ` : ""}${base}`;
    add(`base:${b}`, `${name} (${es.length})`, es.map((e) => e.key), true);
    const terms = new Map<string, Entry[]>();
    es.forEach((e) => { const t = dimsOf(e.metric).term; if (t) terms.set(t, [...(terms.get(t) ?? []), e]); });
    if (terms.size > 1) for (const [t, te] of terms) add(`term:${b}:${t}`, `${name} · ${t} (${te.length})`, te.map((e) => e.key), true);
  }
  return out;
}

/** Multi-metric explorer. Pipelines: every metric as ``stage / metric``, stages switchable. Pick metrics one by one or
 *  as a set (all losses, one loss family, one term across layers, evaluation, LR); draw them as separate charts or
 *  together in one (optionally normalised to their start). Zooming one chart zooms all of them: the section summary
 *  then says, per metric, where it started and ended inside that range. */
export function MetricsExplorer({ runId, keys, baseline, extraRuns = [] }: { runId: string; keys: string[]; baseline: string | null; extraRuns?: string[] }) {
  const cat = useQuery({ queryKey: ["metric-catalog", runId], queryFn: () => api.metricCatalog(runId), staleTime: 60_000 });
  const entries: Entry[] = useMemo(() => cat.data?.entries ?? keys.map((k) => ({ key: k, metric: k, stage: null, label: k })), [cat.data, keys]);
  const stages = cat.data?.stages ?? [];
  const [hiddenStages, setHiddenStages] = useLocalStorage<string[]>("mf.metrics.hiddenStages", []);
  const visible = useMemo(() => entries.filter((e) => !e.stage || !hiddenStages.includes(e.stage)), [entries, hiddenStages]);
  const byKey = useMemo(() => new Map(entries.map((e) => [e.key, e])), [entries]);
  const plottable = useMemo(() => visible.map((e) => e.key), [visible]);
  const sliceKeys = visible.filter((e) => e.metric.includes("[")).map((e) => e.key);
  const sets = useMemo(() => buildSets(visible), [visible]);
  const [pickedRaw, setPicked] = useState<string[] | null>(null);
  const defaults = useMemo(() => {
    const d = visible.filter((e) => DEFAULT_PICKS.includes(e.metric)).map((e) => e.key).slice(0, 6);
    return d.length ? d : visible.filter((e) => !e.metric.includes("[")).map((e) => e.key).slice(0, 4);
  }, [visible]);
  const picked = (pickedRaw ?? defaults).filter((k) => plottable.includes(k));
  const toggleStage = (st: string): void => setHiddenStages(hiddenStages.includes(st) ? hiddenStages.filter((x) => x !== st) : [...hiddenStages, st]);
  const [smoothing, setSmoothing] = useLocalStorage("mf.metrics.smoothing", 0.6);
  const [logY, setLogY] = useLocalStorage("mf.metrics.log", false);
  const [robust, setRobust] = useLocalStorage("mf.metrics.robust", false);
  const [cols, setCols] = useLocalStorage<"auto" | "1" | "2" | "3">("mf.metrics.cols", "auto");
  const [overlay, setOverlay] = useState(true);
  const [together, setTogether] = useState(false);
  const [normalize, setNormalize] = useState(false);
  const [activeSet, setActiveSet] = useState<string | null>(null);
  const [allSets, setAllSets] = useState(false);
  const SETS_SHOWN = 8;
  const [range, setRangeRaw] = useState<[number, number] | null>(null);
  const setRange = (r: [number, number] | null): void => setRangeRaw((cur) => (cur?.[0] === r?.[0] && cur?.[1] === r?.[1] ? cur : r));
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

  const pickSet = (st: MetricSet): void => {
    if (activeSet === st.id) { setActiveSet(null); setPicked(null); setTogether(false); return; }
    setActiveSet(st.id); setPicked(st.keys); setTogether(st.together); setNormalize(st.together && st.id !== "losses" ? false : normalize);
  };
  const toggle = (k: string): void => { setActiveSet(null); setPicked((p) => { const cur = p ?? defaults; return cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]; }); };
  const short = (r: string): string => r.split(":").pop()!.split("+").pop()!;
  const chartsFor = (key: string): ChartSeries[] =>
    runs.flatMap((r, i) => {
      const s = series.data?.[r]?.find((x: MetricSeriesDto) => x.key === key);
      if (!s) return [];
      return [{ name: i === 0 ? short(r) : `${short(r)}${r === baseline ? " (baseline)" : ""}`, x: s.steps, y: s.values, color: PALETTE[i % PALETTE.length], dash: r === baseline }];
    });
  // In one chart, name each curve by what differs between them (e.g. layer=3), the shared part goes to the title.
  const togetherLabels = useMemo(() => {
    const ls = picked.map((k) => byKey.get(k)?.label ?? k);
    const bases = new Set(picked.map((k) => baseOf(byKey.get(k)?.metric ?? k)));
    if (bases.size !== 1 || picked.length < 2) return { title: "", names: ls };
    const dimSets = picked.map((k) => dimsOf(byKey.get(k)?.metric ?? k));
    const keysVary = [...new Set(dimSets.flatMap(Object.keys))].filter((d) => new Set(dimSets.map((x) => x[d])).size > 1);
    const st = byKey.get(picked[0]!)?.stage;
    return { title: `${st ? `${st} / ` : ""}${[...bases][0]}`, names: dimSets.map((x, i) => keysVary.map((d) => `${d}=${x[d] ?? "—"}`).join(" · ") || ls[i]!) };
  }, [picked, byKey]);
  const togetherSeries = (): ChartSeries[] => picked.flatMap((k, i) => {
    const s = series.data?.[runId]?.find((x: MetricSeriesDto) => x.key === k);
    if (!s) return [];
    const first = s.values.find((v) => v !== null && Number.isFinite(v) && v !== 0) ?? 1;
    return [{ name: togetherLabels.names[i] ?? k, x: s.steps, y: normalize ? s.values.map((v) => (v === null ? null : v / first)) : s.values, color: PALETTE[i % PALETTE.length] }];
  });
  const grid = cols === "auto" ? "repeat(auto-fill, minmax(520px, 1fr))" : `repeat(${cols}, minmax(0,1fr))`;
  const chartH = cols === "1" ? 320 : 230;

  return (
    <div className="col" style={{ gap: 12 }}>
      {stages.length > 0 && (
        <div className="card row wrap" style={{ gap: 6, padding: "8px 12px" }} data-testid="stage-toggles" title="Show or hide the metrics of a pipeline stage (remembered by stage name)">
          <span className="faint" style={{ fontSize: 12 }}>Stages</span>
          {stages.map((st) => (
            <button key={st} className={`filter-chip trunc ${hiddenStages.includes(st) ? "" : "on"}`} style={{ maxWidth: 260 }} aria-pressed={!hiddenStages.includes(st)} onClick={() => toggleStage(st)}>{st}</button>
          ))}
          <span className="spacer" />
          {hiddenStages.some((h) => stages.includes(h)) && <button className="btn ghost sm" onClick={() => setHiddenStages(hiddenStages.filter((h) => !stages.includes(h)))}>show all stages</button>}
        </div>
      )}
    <div className="metrics-layout">
      <div className="ml-left"><MetricList title="Metrics" entries={visible.filter((e) => !isLoss(e))} picked={picked} onToggle={toggle} testId="metric-list" /></div>
      <div className="ml-right"><MetricList title="Losses" entries={visible.filter(isLoss)} picked={picked} onToggle={toggle} testId="loss-list" /></div>
      <div className="col ml-main" style={{ gap: 14, minWidth: 0 }}>
        {sets.length > 0 && (
          <div className="card metric-sets" data-testid="metric-sets">
            <span className="faint" style={{ fontSize: 12 }}>Sets</span>
            {(allSets ? sets : sets.filter((st, i) => i < SETS_SHOWN || st.id === activeSet)).map((st) => (
              <button key={st.id} className={`filter-chip trunc ${activeSet === st.id ? "on" : ""}`} style={{ maxWidth: 300 }} aria-pressed={activeSet === st.id} onClick={() => pickSet(st)}>{st.label}</button>
            ))}
            {sets.length > SETS_SHOWN && <button className="btn ghost sm" onClick={() => setAllSets(!allSets)}>{allSets ? "fewer" : `+ ${sets.length - SETS_SHOWN} more`}</button>}
          </div>
        )}
        <div className="card row wrap" style={{ padding: "8px 12px", gap: 16 }}>
          <span className="row muted">Smoothing <input className="range" type="range" min={0} max={0.98} step={0.02} value={smoothing} onChange={(e) => setSmoothing(Number(e.target.value))} /><span className="mono" style={{ width: 32 }}>{smoothing.toFixed(2)}</span></span>
          <span className="row muted">Log Y <Switch on={logY} onChange={setLogY} /></span>
          <span className="row muted" title="Scale the y axis to the 1st–99th percentile: a start-up spike no longer flattens the rest">Ignore spikes <Switch on={robust} onChange={setRobust} /></span>
          <span className="row muted" title="Draw the selected metrics in one chart">One chart <Switch on={together} onChange={setTogether} /></span>
          {together && <span className="row muted" title="Divide each curve by its first value: compare how fast they fall, not their scale">Normalise <Switch on={normalize} onChange={setNormalize} /></span>}
          {!together && baseline && baseline !== runId && <span className="row muted">Overlay baseline <Switch on={overlay} onChange={setOverlay} /><span className="mono faint trunc" style={{ maxWidth: 200 }}>{short(baseline)}</span></span>}
          <span className="spacer" />
          <Seg options={[{ id: "auto", label: "auto" }, { id: "1", label: "1" }, { id: "2", label: "2" }, { id: "3", label: "3" }]} value={cols} onChange={setCols} />
        </div>
        {range && (
          <div className="note" data-testid="section-banner">
            <span>Section: steps <b className="num">{fmtNum(range[0])}</b> – <b className="num">{fmtNum(range[1])}</b> · every chart shows this range</span>
            <span className="spacer" />
          </div>
        )}
        {picked.length === 0 ? <Card><Empty title="Select metrics on the left, or a set above" /></Card> : series.isLoading ? <Loading /> : together ? (
          <div className="card">
            <ExplainableChart runId={runId} metric={picked[0]!} title={`${togetherLabels.title || `${picked.length} metrics`}${normalize ? " · normalised to their start" : ""}`} series={togetherSeries()}
              smoothing={smoothing} showRaw={false} robust={robust} logY={logY && !normalize} syncKey={`run-${runId}`} height={420} onRange={setRange} />
          </div>
        ) : (
          <div className="grid" style={{ gridTemplateColumns: grid }}>
            {picked.map((k) => (
              <div key={k} className="card" style={{ minWidth: 0 }}>
                <ExplainableChart runId={runId} metric={k} title={byKey.get(k)?.label ?? k} baseline={baseline} series={chartsFor(k)}
                  nonFinite={series.data?.[runId]?.find((x: MetricSeriesDto) => x.key === k)?.non_finite ?? 0}
                  smoothing={(byKey.get(k)?.metric ?? k).startsWith("train/") ? smoothing : 0} logY={logY} robust={robust} syncKey={`run-${runId}`} height={chartH} onRange={setRange} />
              </div>
            ))}
          </div>
        )}
        {picked.length > 0 && series.data && <SectionSummary keys={picked} data={series.data[runId] ?? []} range={range} labels={byKey} />}
        {sliceKeys.length > 0 && <SliceBreakdowns data={slices.data} runs={runs} baseline={baseline} />}
      </div>
    </div>
    </div>
  );
}

/** Per metric inside the visible step range (or the whole run): start, end, min, max and the change — how each
 *  loss behaved in that section. */
function SectionSummary({ keys, data, range, labels }: { keys: string[]; data: MetricSeriesDto[]; range: [number, number] | null; labels: Map<string, Entry> }) {
  const rows = keys.flatMap((k) => {
    const s = data.find((x) => x.key === k);
    if (!s) return [];
    const pts = s.steps.map((st, i) => [st, s.values[i]] as const).filter(([st, v]) => v !== null && Number.isFinite(v) && (!range || (st >= range[0] && st <= range[1]))) as [number, number][];
    if (!pts.length) return [];
    const vals = pts.map(([, v]) => v);
    const first = vals[0]!, last = vals[vals.length - 1]!;
    return [{ k, label: labels.get(k)?.label ?? k, first, last, min: Math.min(...vals), max: Math.max(...vals), change: first !== 0 ? (last - first) / Math.abs(first) : null, n: pts.length }];
  });
  if (!rows.length) return null;
  return (
    <Card title={range ? "Section summary" : "Run summary"} sub={range ? `steps ${fmtNum(range[0])} – ${fmtNum(range[1])} (zoom any chart to change the section)` : "the whole run — zoom any chart to study a section"} flush>
      <div className="table-wrap" data-testid="section-summary">
        <table className="t">
          <thead><tr><th>Metric</th><th className="r">start</th><th className="r">end</th><th className="r">min</th><th className="r">max</th><th className="r">change</th><th className="r">points</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.k}>
              <td className="mono trunc" style={{ maxWidth: 420 }}>{r.label}</td>
              <td className="r num">{fmtNum(r.first)}</td><td className="r num">{fmtNum(r.last)}</td>
              <td className="r num">{fmtNum(r.min)}</td><td className="r num">{fmtNum(r.max)}</td>
              <td className="r num" data-sort={r.change ?? ""}>{r.change === null ? "—" : `${r.change >= 0 ? "+" : ""}${(r.change * 100).toFixed(1)} %`}</td>
              <td className="r num faint">{r.n}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </Card>
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
