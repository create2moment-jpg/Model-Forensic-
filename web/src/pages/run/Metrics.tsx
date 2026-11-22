import { useQuery } from "@tanstack/react-query";
import { ChartLine, ChevronDown, ChevronRight, Copy, Eye, EyeOff, Layers, Plus, Search, X } from "lucide-react";
import { MetricFilterBar, applyFilter, emptyFilter, facetsOf, type MetricFilter } from "../compare/Filters";
import { RowActions, type RowAction } from "../../components/RowActions";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { MetricSeriesDto } from "../../api/types";
import { Chart, PALETTE, type ChartSeries } from "../../components/Chart";
import { ExplainableChart } from "../../components/ChartInsight";
import { Card, Empty, FoldAll, Loading, Seg, Switch } from "../../components/ui";
import { fmtNum } from "../../lib/format";
import { useLocalStorage } from "../../lib/hooks";

const parse = (k: string): { base: string; dims: string | null } => {
  const i = k.indexOf("[");
  return i < 0 ? { base: k, dims: null } : { base: k.slice(0, i), dims: k.slice(i + 1, -1) };
};
const DEFAULT_PICKS = ["train/loss", "val/loss", "val/NDS", "val/mAP", "val/AMOTA", "train/lr"];

interface Entry { key: string; metric: string; stage: string | null; label: string }
interface MetricSet { id: string; label: string; keys: string[]; together: boolean; kind: "loss" | "metric"; stage: string }
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

/** Sets of metrics worth looking at together, from the names the run actually has (no project names in code).
 *  Losses: all, per stage, train vs val, every loss family (and per term), per modality. Metrics: evaluation, learning
 *  rate, task weights, gradients, features, system, every sliced family, per modality and per class. */
function buildSets(entries: Entry[]): MetricSet[] {
  const out: MetricSet[] = [];
  const seen = new Set<string>();
  const add = (id: string, label: string, es: Entry[], together: boolean, kind: "loss" | "metric", stage = ""): void => {
    const keys = es.map((e) => e.key);
    const sig = keys.slice().sort().join("|");
    if (!(keys.length > 1 || (keys.length === 1 && id.startsWith("lr"))) || seen.has(sig)) return;  // no duplicate sets
    seen.add(sig);
    out.push({ id, label: `${label} (${keys.length})`, keys: keys.slice(0, 12), together, kind, stage });
  };
  const loss = (e: Entry): boolean => /loss/i.test(e.metric);
  const plain = entries.filter((e) => !e.metric.includes("["));
  const stages = [...new Set(entries.map((e) => e.stage ?? ""))];
  const pre = (st: string): string => (st ? `${st} / ` : "");
  // losses
  add("losses", "All losses", plain.filter(loss), true, "loss");
  for (const st of stages) {
    const es = entries.filter((e) => (e.stage ?? "") === st);
    const pl = es.filter((e) => !e.metric.includes("["));
    if (stages.length > 1) add(`losses:${st}`, `${pre(st)}all losses`, pl.filter(loss), true, "loss", st);
    add(`trainloss:${st}`, `${pre(st)}train losses`, pl.filter((e) => loss(e) && /^train/i.test(e.metric)), true, "loss", st);
    add(`valloss:${st}`, `${pre(st)}val losses`, pl.filter((e) => loss(e) && /^(val|test|eval)/i.test(e.metric)), true, "loss", st);
    // metrics
    add(`eval:${st}`, `${pre(st)}evaluation`, pl.filter((e) => /^(val|test|eval)/i.test(e.metric) && !loss(e)), false, "metric", st);
    add(`lr:${st}`, `${pre(st)}learning rate`, pl.filter((e) => /(^|[/_.])(lr|learning_rate)(\b|$)/i.test(e.metric)), false, "metric", st);
    add(`weights:${st}`, `${pre(st)}task weights`, es.filter((e) => /weight/i.test(e.metric) && !loss(e) && !/decay/i.test(e.metric)), true, "metric", st);
    add(`grad:${st}`, `${pre(st)}gradients`, es.filter((e) => /grad/i.test(e.metric)), true, "metric", st);
    add(`feat:${st}`, `${pre(st)}features`, es.filter((e) => /feat/i.test(e.metric)), true, "metric", st);
    add(`sys:${st}`, `${pre(st)}system`, pl.filter((e) => !e.metric.includes("/") && e.metric.includes(".")), false, "metric", st);
    // per modality and per class, losses and metrics apart
    const facet = (d: string): Map<string, Entry[]> => {
      const m = new Map<string, Entry[]>();
      es.forEach((e) => { const v = facetsOf(e.metric).dims[d]; if (v) m.set(v, [...(m.get(v) ?? []), e]); });
      return m;
    };
    const mods = facet("modality");
    if (mods.size > 1) for (const [m, me] of mods) {
      add(`mod:loss:${st}:${m}`, `${pre(st)}${m} losses`, me.filter(loss), true, "loss", st);
      add(`mod:metric:${st}:${m}`, `${pre(st)}${m} metrics`, me.filter((e) => !loss(e)), true, "metric", st);
    }
    const classes = facet("class");
    if (classes.size > 1 && classes.size <= 30) for (const [c, ce] of classes) add(`class:${st}:${c}`, `${pre(st)}class ${c}`, ce.filter((e) => !loss(e)), false, "metric", st);
  }
  // every sliced family (and per term inside a loss family)
  const byBase = new Map<string, Entry[]>();
  entries.filter((e) => e.metric.includes("[")).forEach((e) => { const b = `${e.stage ?? ""}|${baseOf(e.metric)}`; byBase.set(b, [...(byBase.get(b) ?? []), e]); });
  for (const [b, es] of byBase) {
    const [stage, base] = b.split("|") as [string, string];
    const kind = /loss/i.test(base) ? "loss" : "metric";
    add(`base:${b}`, `${pre(stage)}${base}`, es, true, kind, stage);
    const terms = new Map<string, Entry[]>();
    es.forEach((e) => { const t = dimsOf(e.metric).term; if (t) terms.set(t, [...(terms.get(t) ?? []), e]); });
    if (terms.size > 1) for (const [t, te] of terms) add(`term:${b}:${t}`, `${pre(stage)}${base} · ${t}`, te, true, kind, stage);
  }
  // in each row: stage by stage (the order the pipeline runs), the overall sets first
  const order = new Map(stages.map((st, i) => [st, i]));
  return out.sort((x, y) => (order.get(x.stage) ?? 0) - (order.get(y.stage) ?? 0));
}

/** Multi-metric explorer. Pipelines: every metric as ``stage / metric``, stages switchable. Pick metrics one by one or
 *  as a set (all losses, one loss family, one term across layers, evaluation, LR); draw them as separate charts or
 *  together in one (optionally normalised to their start). Zooming one chart zooms all of them: the section summary
 *  then says, per metric, where it started and ended inside that range. */
export function MetricsExplorer({ runId, keys, baseline, extraRuns = [], initialPicked, initialTogether = false }: { runId: string; keys: string[]; baseline: string | null; extraRuns?: string[]; initialPicked?: string[]; initialTogether?: boolean }) {
  const cat = useQuery({ queryKey: ["metric-catalog", runId], queryFn: () => api.metricCatalog(runId), staleTime: 60_000 });
  const entries: Entry[] = useMemo(() => cat.data?.entries ?? keys.map((k) => ({ key: k, metric: k, stage: null, label: k })), [cat.data, keys]);
  const stages = cat.data?.stages ?? [];
  const [hiddenStages, setHiddenStages] = useLocalStorage<string[]>("mf.metrics.hiddenStages", []);
  const staged = useMemo(() => entries.filter((e) => !e.stage || !hiddenStages.includes(e.stage)), [entries, hiddenStages]);
  // multi-level filter (class, modality, distance, … and text / family) over both lists, the sets and the charts
  const [mfilter, setMfilter] = useState<MetricFilter>(emptyFilter);
  const visible = useMemo(() => { const ok = applyFilter([], mfilter); return staged.filter((e) => ok(e.metric)); }, [staged, mfilter]);
  const [hiddenCharts, setHiddenCharts] = useState<string[]>([]);
  const byKey = useMemo(() => new Map(entries.map((e) => [e.key, e])), [entries]);
  const plottable = useMemo(() => visible.map((e) => e.key), [visible]);
  const sliceKeys = visible.filter((e) => e.metric.includes("[")).map((e) => e.key);
  const sets = useMemo(() => buildSets(visible), [visible]);
  const [pickedRaw, setPicked] = useState<string[] | null>(initialPicked?.length ? initialPicked : null);
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
  const [together, setTogether] = useState(initialTogether);
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
  // row actions of the tables below the charts: plot a metric (compared across the runs), alone, or its family together
  const chartsTop = useRef<HTMLDivElement>(null);
  const toCharts = (): void => { setTimeout(() => chartsTop.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50); };
  const rowActions = (k: string, family?: string[]): RowAction[] => [
    { label: picked.includes(k) ? "Already a chart — show it" : "Add as chart", icon: <Plus size={13} />, onClick: () => { setActiveSet(null); setTogether(false); setPicked((p) => [...new Set([...(p ?? defaults), k])]); toCharts(); }, hint: "a chart of this metric over the run, the other runs overlaid" },
    { label: "Plot only this", icon: <ChartLine size={13} />, onClick: () => { setActiveSet(null); setTogether(false); setPicked([k]); toCharts(); } },
    ...(family && family.length > 1 ? [{ label: `Plot its family together (${Math.min(family.length, 12)})`, icon: <Layers size={13} />, onClick: () => { setActiveSet(null); setPicked(family.slice(0, 12)); setTogether(true); toCharts(); }, hint: "every slice of this metric in one chart" }] : []),
    ...(picked.includes(k) ? [{ label: "Remove the chart", icon: <X size={13} />, onClick: () => setPicked((p) => (p ?? defaults).filter((x) => x !== k)) }] : []),
    { label: "Copy the name", icon: <Copy size={13} />, onClick: () => void navigator.clipboard?.writeText(k) },
  ];
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
      <MetricFilterBar keys={staged.map((e) => e.metric)} value={mfilter} onChange={setMfilter} testId="metrics-filter" compact />
    <div className="metrics-layout">
      <div className="ml-left"><MetricList title="Metrics" entries={visible.filter((e) => !isLoss(e))} picked={picked} onToggle={toggle} testId="metric-list" /></div>
      <div className="ml-right"><MetricList title="Losses" entries={visible.filter(isLoss)} picked={picked} onToggle={toggle} testId="loss-list" /></div>
      <div className="col ml-main" style={{ gap: 14, minWidth: 0 }}>
        {sets.length > 0 && (
          <div className="card metric-sets" data-testid="metric-sets">
            {(["loss", "metric"] as const).map((kind) => {
              const row = sets.filter((st) => st.kind === kind);
              if (!row.length) return null;
              const shown = allSets ? row : row.filter((st, i) => i < SETS_SHOWN || st.id === activeSet);
              return (
                <div key={kind} className="set-row" data-testid={`sets-${kind}`}>
                  <span className="faint set-row-h">{kind === "loss" ? "Losses" : "Metrics"}</span>
                  <div className="set-row-chips">
                    {shown.map((st) => (
                      <button key={st.id} className={`filter-chip trunc ${activeSet === st.id ? "on" : ""}`} style={{ maxWidth: 300 }} aria-pressed={activeSet === st.id} onClick={() => pickSet(st)}>{st.label}</button>
                    ))}
                    {row.length > SETS_SHOWN && <button className="btn ghost sm" onClick={() => setAllSets(!allSets)}>{allSets ? "fewer" : `+ ${row.length - SETS_SHOWN} more`}</button>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <div ref={chartsTop} className="card row wrap" style={{ padding: "8px 12px", gap: 16 }}>
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
        {hiddenCharts.some((k) => picked.includes(k)) && !together && (
          <div className="card row wrap hidden-charts" style={{ gap: 6, padding: "6px 12px" }} data-testid="hidden-charts">
            <span className="faint" style={{ fontSize: 12 }}>Hidden</span>
            {picked.filter((k) => hiddenCharts.includes(k)).map((k) => (
              <button key={k} className="filter-chip trunc" style={{ maxWidth: 260 }} title={`Show ${byKey.get(k)?.label ?? k} again`} onClick={() => setHiddenCharts(hiddenCharts.filter((x) => x !== k))}><Eye size={11} /> {byKey.get(k)?.label ?? k}</button>
            ))}
            <span className="spacer" />
            <button className="btn ghost sm" onClick={() => setHiddenCharts(hiddenCharts.filter((k) => !picked.includes(k)))}>show all</button>
          </div>
        )}
        {picked.length === 0 ? <Card><Empty title="Select metrics on the left, or a set above" /></Card> : series.isLoading ? <Loading /> : together ? (
          <div className="card">
            <ExplainableChart runId={runId} metric={picked[0]!} title={`${togetherLabels.title || `${picked.length} metrics`}${normalize ? " · normalised to their start" : ""}`} series={togetherSeries()}
              smoothing={smoothing} showRaw={false} robust={robust} logY={logY && !normalize} syncKey={`run-${runId}`} height={420} onRange={setRange}
              extraTools={<button className="btn ghost sm icon" title="Remove this chart (clear the selection)" aria-label="Remove chart" onClick={() => { setActiveSet(null); setPicked([]); }}><X size={13} /></button>} />
          </div>
        ) : (
          <div className="grid" style={{ gridTemplateColumns: grid }}>
            {picked.filter((k) => !hiddenCharts.includes(k)).map((k) => (
              <div key={k} className="card" style={{ minWidth: 0 }}>
                <ExplainableChart runId={runId} metric={k} title={byKey.get(k)?.label ?? k} baseline={baseline} series={chartsFor(k)}
                  nonFinite={series.data?.[runId]?.find((x: MetricSeriesDto) => x.key === k)?.non_finite ?? 0}
                  smoothing={(byKey.get(k)?.metric ?? k).startsWith("train/") ? smoothing : 0} logY={logY} robust={robust} syncKey={`run-${runId}`} height={chartH} onRange={setRange}
                  extraTools={<>
                    <button className="btn ghost sm icon" title="Hide this chart (keep it in the selection; show it again from the Hidden strip)" aria-label="Hide chart" onClick={() => setHiddenCharts([...hiddenCharts, k])}><EyeOff size={13} /></button>
                    <button className="btn ghost sm icon" title="Remove this chart" aria-label="Remove chart" onClick={() => { setActiveSet(null); setPicked((p) => (p ?? defaults).filter((x) => x !== k)); }}><X size={13} /></button>
                  </>} />
              </div>
            ))}
          </div>
        )}
        {picked.length > 0 && series.data && <SectionSummary keys={picked} data={series.data[runId] ?? []} range={range} labels={byKey} actions={(k) => rowActions(k, sliceKeys.filter((x) => baseOf(x) === baseOf(k)))} />}
        {sliceKeys.length > 0 && <SliceBreakdowns data={slices.data} runs={runs} baseline={baseline} actions={rowActions} smoothing={smoothing} />}
      </div>
    </div>
    </div>
  );
}

/** Per metric inside the visible step range (or the whole run): start, end, min, max and the change — how each
 *  loss behaved in that section. */
function SectionSummary({ keys, data, range, labels, actions }: { keys: string[]; data: MetricSeriesDto[]; range: [number, number] | null; labels: Map<string, Entry>; actions: (k: string) => RowAction[] }) {
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
    <Card fold="metrics.summary" title={range ? "Section summary" : "Run summary"} sub={range ? `steps ${fmtNum(range[0])} – ${fmtNum(range[1])} (zoom any chart to change the section)` : "the whole run — zoom any chart to study a section"} flush>
      <div className="table-wrap" data-testid="section-summary">
        <table className="t">
          <thead><tr><th>Metric</th><th className="r">start</th><th className="r">end</th><th className="r">min</th><th className="r">max</th><th className="r">change</th><th className="r">points</th><th className="act" /></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.k}>
              <td className="mono trunc" style={{ maxWidth: 420 }}>{r.label}</td>
              <td className="r num">{fmtNum(r.first)}</td><td className="r num">{fmtNum(r.last)}</td>
              <td className="r num">{fmtNum(r.min)}</td><td className="r num">{fmtNum(r.max)}</td>
              <td className="r num" data-sort={r.change ?? ""}>{r.change === null ? "—" : `${r.change >= 0 ? "+" : ""}${(r.change * 100).toFixed(1)} %`}</td>
              <td className="r num faint">{r.n}</td>
              <td className="act"><RowActions actions={actions(r.k)} /></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </Card>
  );
}

function SliceBreakdowns({ data, runs, baseline, actions, smoothing }: { data: Record<string, MetricSeriesDto[]> | undefined; runs: string[]; baseline: string | null; actions: (k: string, family?: string[]) => RowAction[]; smoothing: number }) {
  const [view, setView] = useLocalStorage<"plots" | "table">("mf.slices.view", "plots");
  const [hiddenFams, setHiddenFams] = useLocalStorage<string[]>("mf.slices.hidden", []);
  if (!data) return <Loading label="Loading slice metrics…" />;
  const byBase = new Map<string, Map<string, Record<string, number>>>();
  const keyOf = new Map<string, string>();  // base|dims → the series key (to plot it)
  runs.forEach((r) => (data[r] ?? []).forEach((s) => {
    const { base, dims } = parse(s.key);
    if (!dims) return;
    keyOf.set(`${base}|${dims}`, s.key);
    const v = [...s.values].reverse().find((x) => x !== null);
    if (v === undefined || v === null) return;
    const m = byBase.get(base) ?? new Map();
    const row = m.get(dims) ?? {};
    row[r] = v;
    m.set(dims, row);
    byBase.set(base, m);
  }));
  const all = [...byBase.entries()];
  const families = all.filter(([b]) => !hiddenFams.includes(b));
  const hiddenHere = all.filter(([b]) => hiddenFams.includes(b)).map(([b]) => b);
  const famKeys = (base: string, rows: Map<string, Record<string, number>>): string[] => [...rows.keys()].map((d) => keyOf.get(`${base}|${d}`)!).filter(Boolean);
  return (
    <Card title="Slice breakdowns" fold="metrics.slices" sub={view === "plots" ? "every slice of a metric over the run — where and when did performance change?" : "final value per dimension slice"}
      right={<>
        {view === "plots" && families.length > 1 && <FoldAll what="families" ids={families.map(([b]) => `slice.${b}`)} />}
        <Seg options={[{ id: "plots", label: "Plots" }, { id: "table", label: "Final values" }]} value={view} onChange={setView} />
      </>}>
      {hiddenHere.length > 0 && (
        <div className="row wrap" style={{ gap: 6, marginBottom: 10 }} data-testid="hidden-families">
          <span className="faint" style={{ fontSize: 12 }}>Hidden</span>
          {hiddenHere.map((b) => <button key={b} className="filter-chip mono trunc" style={{ maxWidth: 260 }} title={`Show ${b} again`} onClick={() => setHiddenFams(hiddenFams.filter((x) => x !== b))}><Eye size={11} /> {b}</button>)}
          <button className="btn ghost sm" onClick={() => setHiddenFams(hiddenFams.filter((x) => !hiddenHere.includes(x)))}>show all</button>
        </div>
      )}
      <div className="grid g2" data-testid="slice-breakdowns">
        {families.map(([base, rows], fi) => view === "plots" ? (
          <SliceFamily key={base} base={base} keys={famKeys(base, rows)} runs={runs} baseline={baseline} smoothing={smoothing} defaultFolded={fi >= 4} onHide={() => setHiddenFams([...hiddenFams, base])}
            actions={<RowActions label={`Plot ${base}`} actions={(() => { const fam = famKeys(base, rows); return actions(fam[0]!, fam); })()} />} />
        ) : (
          <div key={base}>
            <div className="sub-h mono row" style={{ textTransform: "none", gap: 6 }}>
              <span className="trunc" style={{ flex: 1 }}>{base}</span>
              <RowActions label={`Plot ${base}`} actions={(() => { const fam = famKeys(base, rows); return actions(fam[0]!, fam); })()} />
            </div>
            <SliceTable rows={rows} runs={runs} baseline={baseline} actionsFor={(dims) => actions(keyOf.get(`${base}|${dims}`)!, famKeys(base, rows))} />
          </div>
        ))}
      </div>
    </Card>
  );
}

function SliceTable({ rows, runs, baseline, actionsFor }: { rows: Map<string, Record<string, number>>; runs: string[]; baseline: string | null; actionsFor: (dims: string) => RowAction[] }) {
  const max = Math.max(...[...rows.values()].flatMap((r) => Object.values(r)));
  return (
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
              <td className="act"><RowActions actions={actionsFor(dims)} /></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** One metric family (e.g. recall[class=…]) as one chart: a curve per slice, the baseline dashed in the same colour.
 *  Folded families fetch nothing. */
function SliceFamily({ base, keys, runs, baseline, smoothing, defaultFolded, actions, onHide }: { base: string; keys: string[]; runs: string[]; baseline: string | null; smoothing: number; defaultFolded: boolean; actions: React.ReactNode; onHide: () => void }) {
  const [off, setOff] = useState<string[]>([]);  // slices switched off in this chart (click a legend chip)
  const id = `slice.${base}`;
  const [folded, setFolded] = useLocalStorage<boolean>(`mf.fold.${id}`, defaultFolded);
  useEffect(() => {
    const on = (e: Event): void => { const d = (e as CustomEvent<{ ids: string[]; folded: boolean }>).detail; if (d.ids.includes(id)) setFolded(d.folded); };
    window.addEventListener("mf-fold", on);
    return () => window.removeEventListener("mf-fold", on);
  }, [id, setFolded]);
  const shown = keys.slice(0, 12);
  const q = useQuery({
    queryKey: ["series", "slice-family", runs, shown],
    queryFn: async () => Object.fromEntries(await Promise.all(runs.map(async (r) => [r, await api.metrics(r, shown, 400)] as const))),
    enabled: !folded,
  });
  // name each slice by what differs between them (module=radar_fpn · scale=2 → radar_fpn · 2 when only those vary)
  const parts = shown.map((k) => Object.fromEntries((parse(k).dims ?? k).split(",").map((kv) => { const [a, b] = kv.split("="); return [a!.trim(), (b ?? a!).trim()]; })));
  const vary = [...new Set(parts.flatMap(Object.keys))].filter((d) => new Set(parts.map((x) => x[d])).size > 1);
  const nameOf = new Map(shown.map((k, i) => [k, (vary.length ? vary : Object.keys(parts[i]!)).map((d) => parts[i]![d] ?? "—").join(" · ")]));
  const name = (k: string): string => nameOf.get(k) ?? k;
  const hasBase = Boolean(baseline && runs.indexOf(baseline) > 0);
  const series: ChartSeries[] = q.data ? runs.flatMap((r, ri) => shown.flatMap((k, i) => {
    if ((ri > 0 && r !== baseline) || off.includes(k)) return [];  // the other runs: in the table view and the main charts
    const s = q.data[r]?.find((x: MetricSeriesDto) => x.key === k);
    if (!s) return [];
    return [{ name: r === baseline && ri > 0 ? `${name(k)} (baseline)` : name(k), x: s.steps, y: s.values, color: PALETTE[i % PALETTE.length], dash: ri > 0 }];
  })) : [];
  return (
    <div className="slice-fam" data-testid="slice-family">
      <div className="slice-fam-head">
        <button type="button" className="fold-btn" aria-expanded={!folded} aria-label={`${folded ? "Unfold" : "Fold"} ${base}`} onClick={() => setFolded(!folded)}>
          {folded ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        </button>
        <span className="mono trunc" style={{ flex: 1, cursor: "pointer" }} onClick={() => setFolded(!folded)}>{base}</span>
        <span className="faint">{keys.length} slices{keys.length > shown.length ? ` · first ${shown.length}` : ""}</span>
        {actions}
        <button type="button" className="btn ghost sm icon" title="Hide this family (show it again from the Hidden strip)" aria-label={`Hide ${base}`} onClick={onHide}><EyeOff size={13} /></button>
      </div>
      {!folded && (q.isLoading ? <Loading /> : !q.data ? <Empty title="No points for these slices" /> :
        <>
          <Chart series={series} height={210} smoothing={smoothing} showRaw={false} syncKey={`run-${runs[0]}`} hideLegend />
          <div className="slice-legend">
            {vary.length > 0 && <span className="faint mono">{vary.join(" · ")}</span>}
            {shown.map((k, i) => (
              <button type="button" key={k} className={`trunc ${off.includes(k) ? "off" : ""}`} title={`${k} — click to ${off.includes(k) ? "show" : "hide"} this curve`} aria-pressed={!off.includes(k)}
                onClick={() => setOff(off.includes(k) ? off.filter((x) => x !== k) : [...off, k])}><i style={{ background: PALETTE[i % PALETTE.length] }} />{name(k)}</button>
            ))}
            {hasBase && <span className="faint">– – baseline</span>}
          </div>
        </>)}
    </div>
  );
}
