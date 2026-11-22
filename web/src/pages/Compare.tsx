import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, Bot, FileText, GitCompare, Info, Play, Plus, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { MetricDelta } from "../api/types";
import { CompareCurves } from "../components/CompareCurves";
import { RunPicker } from "../components/RunPicker";
import { SubjectPicker } from "../components/SubjectPicker";
import { Card, DeltaBar, Empty, ErrorBox, Loading, Pill, Seg } from "../components/ui";
import { fmtNum, fmtPct, fmtValue } from "../lib/format";
import { MetricsExplorer } from "./run/Metrics";
import { RunsOverview } from "./compare/RunsOverview";

const shortName = (v: string): string => (v.startsWith("group:") ? `${v.slice(6).split("+").length} runs: ${v.slice(6).split("+").map((m) => m.split(":").pop()).join(" + ")}` : v.split(":").pop() ?? v);
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
      {!ready ? <Card><Empty icon={<GitCompare size={28} />} title="Pick two runs">Tip: in the Runs table, select two runs and press Compare. The first selected becomes the baseline.</Empty></Card> :
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
