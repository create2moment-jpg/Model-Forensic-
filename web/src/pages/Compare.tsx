import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, Bot, ChartSpline, Copy, FileText, Filter, Layers, GitCompare, Info, LineChart, Play, Plus, Scale, ShieldAlert, Wrench, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { MetricDelta } from "../api/types";
import { CompareCurves } from "../components/CompareCurves";
import { RunPicker } from "../components/RunPicker";
import { SubjectPicker } from "../components/SubjectPicker";
import { Card, DeltaBar, Empty, ErrorBox, Loading, Pill, Seg, Tabs } from "../components/ui";
import { fmtNum, fmtPct, fmtValue } from "../lib/format";
import { MetricsExplorer } from "./run/Metrics";
import { UncertaintyTab } from "./run/Uncertainty";
import { PlotsTab } from "./run/Plots";
import { FindingsTab } from "./run/Findings";
import { SetupCompare } from "./compare/SetupCompare";
import { MetricFilterBar, applyFilter, emptyFilter, parseMetric, type MetricFilter } from "./compare/Filters";
import { RowActions, type RowAction } from "../components/RowActions";
import { ConfigPath, MetricName } from "../components/MetricTile";
import { RunsOverview } from "./compare/RunsOverview";

const shortName = (v: string): string => (v.startsWith("group:") ? `${v.slice(6).split("+").length} runs: ${v.slice(6).split("+").map((m) => m.split(":").pop()).join(" + ")}` : v.split(":").pop() ?? v);
type CTab = "overview" | "metrics" | "uncertainty" | "plots" | "setup" | "findings";
const worse = (d: MetricDelta): boolean | null => (d.higher_is_better === null || d.relative === null ? null : d.higher_is_better ? d.relative < 0 : d.relative > 0);
const VERDICT_TONE = { regressed: "bad", improved: "good", unchanged: "outline", unknown: "outline" } as const;

export function ComparePage() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const baseline = sp.get("baseline");
  // Candidates: ?candidate=…&more=…,… — each one run or a run group (group:<run>+<run>). ?focus=i: the one in detail.
  const candidates = [sp.get("candidate"), ...(sp.get("more") ?? "").split(",")].filter((x): x is string => !!x);
  const focus = Math.min(Number(sp.get("focus") ?? 0) || 0, Math.max(0, candidates.length - 1));
  const candidate = candidates[focus] ?? null;
  const more = candidates.filter((_, i) => i !== focus);
  const setCandidates = (list: string[], nextFocus = focus): void => {
    const n = new URLSearchParams(sp);
    const l = list.filter(Boolean);
    if (l[0]) n.set("candidate", l[0]); else n.delete("candidate");
    if (l.length > 1) n.set("more", l.slice(1).join(",")); else n.delete("more");
    const f = Math.min(nextFocus, Math.max(0, l.length - 1));
    if (f) n.set("focus", String(f)); else n.delete("focus");
    setSp(n);
  };
  const [addingCand, setAddingCand] = useState(false);
  // ?stages=<run>|<stage>,… — explicit per-run stage choice for pipelines (default: the headline / last stage)
  const stageMap: Record<string, string> = Object.fromEntries((sp.get("stages") ?? "").split(",").filter((x) => x.includes("|")).map((x) => x.split("|") as [string, string]));
  const setStage = (run: string, stage: string): void => {
    const m = { ...stageMap, [run]: stage };
    const n = new URLSearchParams(sp);
    const enc_ = Object.entries(m).filter(([, v]) => v).map(([k, v]) => `${k}|${v}`).join(",");
    if (enc_) n.set("stages", enc_); else n.delete("stages");
    setSp(n);
  };
  const setMore = (m: string[]): void => setCandidates([candidate!, ...m], 0);
  const [view, setView] = useState<"all" | "moved" | "regressed">("moved");
  const [slices, setSlices] = useState(true);
  const [mfilter, setMfilter] = useState<MetricFilter>(emptyFilter);
  const ctab = (sp.get("ctab") as CTab | null) ?? "overview";
  const setCtab = (t: CTab): void => { const n = new URLSearchParams(sp); if (t === "overview") n.delete("ctab"); else n.set("ctab", t); setSp(n, { replace: true }); };
  const ready = !!baseline && !!candidate;
  const cmp = useQuery({ queryKey: ["compare", baseline, candidate], queryFn: () => api.compare(baseline!, candidate!), enabled: ready && ctab === "overview" });

  const deltaKeys = useMemo(() => (cmp.data?.deltas ?? []).map((d) => d.metric), [cmp.data]);
  const deltas = useMemo(() => (cmp.data?.deltas ?? [])
    .filter((d) => slices || !d.metric.includes("["))
    .filter((d) => applyFilter(deltaKeys, mfilter)(d.metric))
    .filter((d) => view === "all" || (view === "regressed" ? d.verdict === "regressed" : d.verdict === "regressed" || d.verdict === "improved"))
    .sort((a, b) => (a.verdict === "regressed" ? 0 : 1) - (b.verdict === "regressed" ? 0 : 1) || Math.abs(b.relative ?? 0) - Math.abs(a.relative ?? 0)), [cmp.data, view, slices, mfilter, deltaKeys]);
  // the chart button of a delta: open the Metrics tab with this metric (or its whole family) plotted for every run
  const showPlot = (keys: string[], together: boolean): void => {
    const n = new URLSearchParams(sp);
    n.set("ctab", "metrics"); n.set("mk", keys.slice(0, 12).join("|"));
    if (together) n.set("mt", "1"); else n.delete("mt");
    setSp(n);
  };
  const plotActions = (k: string): RowAction[] => {
    const fam = deltaKeys.filter((x) => parseMetric(x).family === parseMetric(k).family);
    return [
      { label: "Plot it across the runs", icon: <LineChart size={13} />, onClick: () => showPlot([k], false), hint: "the Metrics tab: baseline, candidate and the other candidates over the run" },
      ...(fam.length > 1 ? [{ label: `Plot its family together (${Math.min(fam.length, 12)})`, icon: <Layers size={13} />, onClick: () => showPlot(fam, true) }] : []),
      { label: "Filter the table to its family", icon: <Filter size={13} />, onClick: () => setMfilter({ ...emptyFilter, family: parseMetric(k).family }) },
      { label: "Copy the name", icon: <Copy size={13} />, onClick: () => void navigator.clipboard?.writeText(k) },
    ];
  };
  const changes = cmp.data?.config_diff.changes ?? [];
  const meaningful = changes.filter((c) => !c.is_noise);

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Compare runs</h1><div className="sub">Noise-aware metric deltas, semantic config diff, and where and why 2–10 runs diverge.</div></div>
        {ready && (
          <div className="actions">
            <button className="btn" onClick={() => nav(`/runs/${encodeURIComponent(candidate!)}?tab=findings&analyze=1&baseline=${encodeURIComponent(baseline!)}`)}><Play size={14} /> Analyze</button>
            <button className="btn" onClick={() => nav(`/reports?run=${encodeURIComponent(candidate!)}&baseline=${encodeURIComponent(baseline!)}`)}><FileText size={14} /> Report</button>
            <button className="btn primary" onClick={() => nav(`/ai?run=${encodeURIComponent(candidate!)}&baseline=${encodeURIComponent(baseline!)}`)}><Bot size={14} /> Why is it worse?</button>
          </div>
        )}
      </div>
      <div className="card row wrap" style={{ padding: 12, marginBottom: 14, gap: 12 }}>
        <label className="field">Baseline<SubjectPicker value={baseline} onChange={(v) => { const n = new URLSearchParams(sp); if (v) n.set("baseline", v); else n.delete("baseline"); setSp(n); }} placeholder="Baseline run…" width={300} label="Baseline" /></label>
        <button className="btn icon" style={{ marginTop: 18 }} title="Swap the baseline and the candidate in focus" disabled={!ready}
          onClick={() => { const n = new URLSearchParams(sp); const l = [...candidates]; l[focus] = baseline!; n.set("baseline", candidate!); n.set("candidate", l[0]!); if (l.length > 1) n.set("more", l.slice(1).join(",")); setSp(n); }}><ArrowLeftRight size={14} /></button>
        <div className="field">
          <span>Candidate{candidates.length > 1 ? `s (${candidates.length})` : ""}</span>
          <div className="row wrap" style={{ gap: 8, alignItems: "flex-start" }}>
            {(candidates.length ? candidates : [""]).map((c, i) => (
              <div key={`${i}-${c}`} className="row" style={{ gap: 4, alignItems: "flex-start" }}>
                <SubjectPicker value={c || null} width={280} placeholder="Candidate run…" label={`Candidate ${i + 1}`}
                  onChange={(v) => { const l = [...candidates]; if (v) l[i] = v; else l.splice(i, 1); setCandidates(l, v || i > focus ? focus : Math.max(0, focus - 1)); }} />
                {candidates.length > 1 && <button className="btn ghost sm icon" aria-label={`Remove candidate ${i + 1}`} title="Remove this candidate" onClick={() => setCandidates(candidates.filter((_, j) => j !== i), i < focus ? focus - 1 : i === focus ? 0 : focus)}><X size={13} /></button>}
              </div>
            ))}
            {candidates.length > 0 && candidates.length < 10 && (addingCand
              ? <RunPicker value={null} placeholder="Another candidate…" width={280} onChange={(v) => { setCandidates([...candidates, v]); setAddingCand(false); }} />
              : <button className="btn sm" onClick={() => setAddingCand(true)}><Plus size={13} /> Candidate</button>)}
          </div>
        </div>
      </div>
      {ready && <div style={{ marginBottom: 14 }}><RunsOverview baseline={baseline!} candidates={candidates} focus={focus} onFocus={(i) => setCandidates(candidates, i)} /></div>}
      {ready && candidates.length > 1 && (
        <div className="row wrap" style={{ gap: 6, marginBottom: 10 }} data-testid="candidate-tabs">
          <span className="faint">Details for</span>
          {candidates.map((c, i) => <button key={c} className={`filter-chip ${i === focus ? "on" : ""}`} aria-pressed={i === focus} onClick={() => setCandidates(candidates, i)}>{shortName(c)}</button>)}
          <span className="faint">vs the baseline</span>
        </div>
      )}
      {ready && (
        <div style={{ marginBottom: 12 }}>
          <Tabs<CTab> value={ctab} onChange={setCtab} tabs={[
            { id: "overview", label: "Deltas & config", icon: <GitCompare size={14} /> },
            { id: "metrics", label: "Metrics", icon: <LineChart size={14} /> },
            { id: "uncertainty", label: "Uncertainty", icon: <Scale size={14} /> },
            { id: "plots", label: "Plots", icon: <ChartSpline size={14} /> },
            { id: "setup", label: "Setup", icon: <Wrench size={14} /> },
            { id: "findings", label: "Findings", icon: <ShieldAlert size={14} /> },
          ]} />
        </div>
      )}
      {ready && ctab === "metrics" && <MetricsExplorer key={`${candidate}|${sp.get("mk") ?? ""}`} runId={candidate!} keys={[]} baseline={baseline} extraRuns={more}
        initialPicked={(sp.get("mk") ?? "").split("|").filter(Boolean)} initialTogether={sp.get("mt") === "1"} />}
      {ready && ctab === "uncertainty" && <UncertaintyTab key={`${candidate}|${baseline}`} runId={candidate!} initialBaseline={baseline} />}
      {ready && ctab === "plots" && <PlotsTab runId={candidate!} />}
      {ready && ctab === "setup" && <SetupCompare ids={[baseline!, ...candidates]} />}
      {ready && ctab === "findings" && <FindingsTab runId={candidate!} baseline={baseline} autoRun={false} stored={[]} />}
      {ready && ctab !== "overview" ? null : !ready ? <Card><Empty icon={<GitCompare size={28} />} title="Pick two runs">Tip: in the Runs table, select two runs and press Compare. The first selected becomes the baseline.</Empty></Card> :
        cmp.isLoading ? <Loading label="Comparing metrics and configurations (loads both runs' histories)…" /> : cmp.error ? <ErrorBox error={cmp.error} /> : cmp.data && (
          <div className="col" style={{ gap: 14 }}>
            <div className="row wrap" style={{ gap: 6 }}>
              <Pill tone={cmp.data.baseline_replicates.length >= 2 ? "good" : "warn"}>
                {cmp.data.baseline_replicates.length >= 2 ? `significance from ${cmp.data.baseline_replicates.length + 1} baseline seeds` : "single-seed: threshold-based significance"}
              </Pill>
              <Pill tone="bad">{cmp.data.deltas.filter((d) => d.verdict === "regressed").length} regressed</Pill>
              <Pill tone="good">{cmp.data.deltas.filter((d) => d.verdict === "improved").length} improved</Pill>
              <Pill tone="outline">{meaningful.length} config changes</Pill>
              {cmp.data.duration_ratio && <Pill tone="outline">runtime ×{cmp.data.duration_ratio.toFixed(2)}</Pill>}
            </div>
            {cmp.data.baseline_replicates.length < 2 && (
              <div className="note warn"><Info size={14} style={{ marginTop: 2 }} />Without baseline replicates, small deltas may be seed noise. Tag baseline seeds with a shared <code>group</code> tag to get z-scores.</div>
            )}
            <MetricFilterBar keys={deltaKeys} value={mfilter} onChange={setMfilter} />
            <div className="split" style={{ gridTemplateColumns: "minmax(0,1fr) 440px" }}>
              <Card title="Metrics" sub={`${deltas.length} shown`} flush right={<>
                <label className="row muted" style={{ fontSize: 12 }}><input type="checkbox" checked={slices} onChange={(e) => setSlices(e.target.checked)} style={{ accentColor: "var(--accent)" }} /> slices</label>
                <Seg options={[{ id: "moved", label: "Moved" }, { id: "regressed", label: "Regressed" }, { id: "all", label: "All" }]} value={view} onChange={setView} />
              </>}>
                <div className="table-wrap" style={{ maxHeight: 520 }}>
                  <table className="t compact fixed">
                    <thead><tr><th style={{ width: "34%" }}>Metric</th><th className="r">Baseline</th><th className="r">Candidate</th><th className="r">Δ%</th><th style={{ width: 120 }} /><th className="r" style={{ width: 56 }}>z</th><th style={{ width: 100 }}>Verdict</th><th className="act" /></tr></thead>
                    <tbody>
                      {deltas.map((d) => (
                        <tr key={d.metric}>
                          <td><MetricName k={d.metric} /></td>
                          <td className="r num faint">{fmtNum(d.baseline)}</td>
                          <td className="r num">{fmtNum(d.candidate)}</td>
                          <td className="r num" style={{ color: d.verdict === "regressed" ? "var(--bad)" : d.verdict === "improved" ? "var(--good)" : undefined }}>{fmtPct(d.relative)}</td>
                          <td><DeltaBar rel={d.relative} worse={worse(d)} /></td>
                          <td className="r num faint">{d.z_score !== null ? d.z_score.toFixed(1) : "—"}</td>
                          <td><Pill tone={VERDICT_TONE[d.verdict]}>{d.verdict}</Pill></td>
                          <td className="act"><RowActions actions={plotActions(d.metric)} /></td>
                        </tr>
                      ))}
                      {deltas.length === 0 && <tr><td colSpan={8} className="faint" style={{ padding: 20, textAlign: "center" }}>{(() => {
                        const n = (cmp.data?.deltas ?? []).filter((d) => (slices || !d.metric.includes("[")) && applyFilter(deltaKeys, mfilter)(d.metric)).length;
                        return view !== "all" && n ? <>No {view === "regressed" ? "regressed" : "moved"} metric matches — <button className="btn ghost sm" onClick={() => setView("all")}>show all {n}</button></> : "Nothing in this view.";
                      })()}</td></tr>}
                    </tbody>
                  </table>
                </div>
              </Card>
              <Card title="Configuration diff" sub={`${meaningful.length} meaningful · ${changes.length - meaningful.length} runtime-only`} flush>
                <div className="table-wrap" style={{ maxHeight: 520 }}>
                  <table className="t compact fixed">
                    <tbody>
                      {meaningful.map((c) => (
                        <tr key={c.path}>
                          <td style={{ width: "55%" }}><ConfigPath path={c.path} /><div className="row" style={{ gap: 6, marginTop: 2 }}><span className="tag">{c.category}</span><span className="faint" style={{ fontSize: 11 }}>{c.kind}</span></div></td>
                          <td className="mono" style={{ fontSize: 11.5 }}><div className="faint trunc" style={{ textDecoration: "line-through" }} title={fmtValue(c.baseline)}>{c.kind === "added" ? "" : fmtValue(c.baseline)}</div><div className="trunc" title={fmtValue(c.candidate)}>{c.kind === "removed" ? "" : fmtValue(c.candidate)}{c.relative_change !== null && <span className="faint"> ({fmtPct(c.relative_change, 0)})</span>}</div></td>
                        </tr>
                      ))}
                      {meaningful.length === 0 && <tr><td className="faint" style={{ padding: 20 }}>Identical configuration — suspect seed variance, code or data changes.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
            <CompareCurves runs={[baseline!, candidate!, ...more]} onAdd={(r) => setMore([...more, r])} onRemove={(r) => setMore(more.filter((x) => x !== r))}
              stages={stageMap} onStage={setStage} />

          </div>
        )}
    </div>
  );
}
