import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, Bot, FileText, GitCompare, Info, Play } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { MetricDelta } from "../api/types";
import { CompareCurves } from "../components/CompareCurves";
import { RunPicker } from "../components/RunPicker";
import { Card, DeltaBar, Empty, ErrorBox, Loading, Pill, Seg } from "../components/ui";
import { fmtNum, fmtPct, fmtValue } from "../lib/format";
import { MetricsExplorer } from "./run/Metrics";

const worse = (d: MetricDelta): boolean | null => (d.higher_is_better === null || d.relative === null ? null : d.higher_is_better ? d.relative < 0 : d.relative > 0);
const VERDICT_TONE = { regressed: "bad", improved: "good", unchanged: "outline", unknown: "outline" } as const;

export function ComparePage() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const baseline = sp.get("baseline");
  const candidate = sp.get("candidate");
  const more = (sp.get("more") ?? "").split(",").filter(Boolean);
  // ?stages=<run>|<stage>,… — explicit per-run stage choice for pipelines (default: the headline / last stage)
  const stageMap: Record<string, string> = Object.fromEntries((sp.get("stages") ?? "").split(",").filter((x) => x.includes("|")).map((x) => x.split("|") as [string, string]));
  const setStage = (run: string, stage: string): void => {
    const m = { ...stageMap, [run]: stage };
    const n = new URLSearchParams(sp);
    const enc_ = Object.entries(m).filter(([, v]) => v).map(([k, v]) => `${k}|${v}`).join(",");
    if (enc_) n.set("stages", enc_); else n.delete("stages");
    setSp(n);
  };
  const setMore = (m: string[]): void => { const n = new URLSearchParams(sp); if (m.length) n.set("more", m.join(",")); else n.delete("more"); setSp(n); };
  const [view, setView] = useState<"all" | "moved" | "regressed">("moved");
  const [slices, setSlices] = useState(true);
  const set = (k: string, v: string): void => { const n = new URLSearchParams(sp); n.set(k, v); setSp(n); };
  const ready = !!baseline && !!candidate;
  const cmp = useQuery({ queryKey: ["compare", baseline, candidate], queryFn: () => api.compare(baseline!, candidate!), enabled: ready });
  const ov = useQuery({ queryKey: ["overview", candidate, baseline], queryFn: () => api.overview(candidate!, baseline), enabled: ready });

  const deltas = useMemo(() => (cmp.data?.deltas ?? [])
    .filter((d) => slices || !d.metric.includes("["))
    .filter((d) => view === "all" || (view === "regressed" ? d.verdict === "regressed" : d.verdict === "regressed" || d.verdict === "improved"))
    .sort((a, b) => (a.verdict === "regressed" ? 0 : 1) - (b.verdict === "regressed" ? 0 : 1) || Math.abs(b.relative ?? 0) - Math.abs(a.relative ?? 0)), [cmp.data, view, slices]);
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
        <label className="field">Baseline<RunPicker value={baseline} onChange={(v) => set("baseline", v)} placeholder="Baseline run…" width={320} /></label>
        <button className="btn icon" style={{ marginTop: 18 }} title="Swap" disabled={!ready} onClick={() => setSp({ baseline: candidate!, candidate: baseline! })}><ArrowLeftRight size={14} /></button>
        <label className="field">Candidate<RunPicker value={candidate} onChange={(v) => set("candidate", v)} placeholder="Candidate run…" width={320} /></label>
        {more.length > 0 && <span className="faint" style={{ marginTop: 18 }}>+ {more.length} more run(s) in the curves below</span>}
      </div>
      {!ready ? <Card><Empty icon={<GitCompare size={28} />} title="Pick two runs">Tip: in the Runs table, select two runs and press Compare. The first selected becomes the baseline.</Empty></Card> :
        cmp.isLoading ? <Loading label="Comparing…" /> : cmp.error ? <ErrorBox error={cmp.error} /> : cmp.data && (
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
            <div className="split" style={{ gridTemplateColumns: "minmax(0,1fr) 440px" }}>
              <Card title="Metrics" flush right={<>
                <label className="row muted" style={{ fontSize: 12 }}><input type="checkbox" checked={slices} onChange={(e) => setSlices(e.target.checked)} style={{ accentColor: "var(--accent)" }} /> slices</label>
                <Seg options={[{ id: "moved", label: "Moved" }, { id: "regressed", label: "Regressed" }, { id: "all", label: "All" }]} value={view} onChange={setView} />
              </>}>
                <div className="table-wrap" style={{ maxHeight: 520 }}>
                  <table className="t compact">
                    <thead><tr><th>Metric</th><th className="r">Baseline</th><th className="r">Candidate</th><th className="r">Δ%</th><th style={{ width: 150 }} /><th className="r">z</th><th>Verdict</th></tr></thead>
                    <tbody>
                      {deltas.map((d) => (
                        <tr key={d.metric}>
                          <td className="mono">{d.metric}</td>
                          <td className="r num faint">{fmtNum(d.baseline)}</td>
                          <td className="r num">{fmtNum(d.candidate)}</td>
                          <td className="r num" style={{ color: d.verdict === "regressed" ? "var(--bad)" : d.verdict === "improved" ? "var(--good)" : undefined }}>{fmtPct(d.relative)}</td>
                          <td><DeltaBar rel={d.relative} worse={worse(d)} /></td>
                          <td className="r num faint">{d.z_score !== null ? d.z_score.toFixed(1) : "—"}</td>
                          <td><Pill tone={VERDICT_TONE[d.verdict]}>{d.verdict}</Pill></td>
                        </tr>
                      ))}
                      {deltas.length === 0 && <tr><td colSpan={7} className="faint" style={{ padding: 20, textAlign: "center" }}>Nothing in this view.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </Card>
              <Card title="Configuration diff" sub={`${meaningful.length} meaningful · ${changes.length - meaningful.length} runtime-only`} flush>
                <div className="table-wrap" style={{ maxHeight: 520 }}>
                  <table className="t compact">
                    <tbody>
                      {meaningful.map((c) => (
                        <tr key={c.path}>
                          <td className="wrap"><div className="mono">{c.path}</div><div className="row" style={{ gap: 6, marginTop: 2 }}><span className="tag">{c.category}</span><span className="faint" style={{ fontSize: 11 }}>{c.kind}</span></div></td>
                          <td className="mono wrap" style={{ fontSize: 11.5 }}><div className="faint" style={{ textDecoration: "line-through" }}>{c.kind === "added" ? "" : fmtValue(c.baseline)}</div><div>{c.kind === "removed" ? "" : fmtValue(c.candidate)}{c.relative_change !== null && <span className="faint"> ({fmtPct(c.relative_change, 0)})</span>}</div></td>
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
            {ov.data && <MetricsExplorer runId={candidate!} keys={ov.data.metric_keys} baseline={baseline} />}
          </div>
        )}
    </div>
  );
}
