import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { Bot, CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import type { PlotSpecV1, PlotValidation } from "../../api/types";
import { PlotView } from "../../components/PlotView";
import { Card, ErrorBox, Loading } from "../../components/ui";

/** Plots as validated specs: rule-based suggestions, the local-LLM plotter (every proposal is validated — it can
 *  only plot keys the run has), and a spec editor with schema + grounding checks. */
const short = (r: string): string => r.split(":").pop()!.split("+").pop()!;

/** The runs switched off on the compare page leave the plot (each metric keeps its colour). */
export function visible(spec: PlotSpecV1, hidden: string[]): PlotSpecV1 {
  if (!hidden.length) return spec;
  const series = spec.series.filter((s) => !hidden.includes(s.run));
  return series.length ? { ...spec, series } : spec;
}

/** At most this many curves in one plot (the plot spec's bound, domain/plotspec.py). */
export const MAX_SERIES = 12;

/** The same plot for several runs: every series repeated per run, labelled ``run · metric`` (the baseline marked). */
export function overlay(spec: PlotSpecV1, runId: string, others: string[], baseline: string | null, has: (run: string, key: string) => boolean = () => true, stageOf: (run: string) => string = () => ""): PlotSpecV1 {
  if (!others.length) return spec;
  const name = (r: string, k: string, l?: string): string => `${short(r)}${r === baseline ? " (baseline)" : ""} · ${l || k}`;
  // a plot holds at most MAX_SERIES curves: keep as many metrics as fit with every run's copy of each
  const runs = Math.min(others.length, MAX_SERIES - 1);
  const keep = Math.max(1, Math.floor(MAX_SERIES / (runs + 1)));
  const own = spec.series.slice(0, keep);
  const mine = own.filter((s) => s.run === runId);
  return {
    ...spec, id: `${spec.id}-vs`,
    series: [...own.map((s) => ({ ...s, label: name(s.run, s.key, s.label) })),
      ...others.slice(0, runs).flatMap((r) => mine.filter((s) => has(r, s.key)).map((s) => ({ ...s, run: r, stage: stageOf(r), label: name(r, s.key, s.label) })))].slice(0, MAX_SERIES),
  };
}

export function PlotsTab({ runId, baseline = null, extraRuns = [], stage = "", hidden = [] }: { runId: string; baseline?: string | null; extraRuns?: string[]; stage?: string; hidden?: string[] }) {
  const others = [...(baseline && baseline !== runId ? [baseline] : []), ...extraRuns.filter((r) => r !== runId && r !== baseline)];
  // only the metrics each other run logged (a plot never asks a run for a metric it does not have)
  const cats = useQueries({ queries: others.map((r) => ({ queryKey: ["metric-catalog", r], queryFn: () => api.metricCatalog(r), staleTime: 60_000 })) });
  // the page's stage in every other run that has it (else that run's headline)
  const stageOf = (r: string): string => { const c = cats[others.indexOf(r)]?.data; return stage && c?.stages.includes(stage) ? stage : ""; };
  const keySets = Object.fromEntries(others.map((r, i) => [r, new Set((cats[i]?.data?.entries ?? []).filter((e) => !stageOf(r) || e.stage === stageOf(r)).flatMap((e) => [e.key, e.metric]))]));
  const has = (r: string, k: string): boolean => keySets[r]?.has(k) ?? false;
  const catsReady = cats.every((c) => !c.isLoading);
  const sug = useQuery({ queryKey: ["plot-suggest", runId, stage], queryFn: () => api.plotSuggest(runId, stage || null) });
  const [question, setQuestion] = useState("");
  const ai = useMutation({ mutationFn: () => api.plotAi(runId, question) });
  const [text, setText] = useState("");
  const [check, setCheck] = useState<PlotValidation | null>(null);
  const [custom, setCustom] = useState<PlotSpecV1 | null>(null);
  const validate = async (): Promise<void> => {
    let doc: unknown;
    try { doc = JSON.parse(text); } catch (e) { setCheck({ ok: false, errors: [`not JSON: ${String(e)}`], warnings: [], spec: null }); return; }
    const v = await api.plotValidate(doc);
    setCheck(v);
    setCustom(v.ok ? v.spec : null);
  };
  if (sug.isLoading || !catsReady) return <Loading />;
  if (sug.error || !sug.data) return <ErrorBox error={sug.error} />;
  const aiSpecs = ai.data ? (ai.data.specs.length ? ai.data.specs : ai.data.fallback ?? []) : [];
  return (
    <div className="col" style={{ gap: 14 }}>
      <Card title={<span className="row"><Bot size={15} /> Ask the plotter</span>} sub="The local LLM proposes plot specs; each one is validated against the run's metric keys before it is drawn">
        <form className="row" onSubmit={(e) => { e.preventDefault(); ai.mutate(); }}>
          <input className="input" style={{ flex: 1 }} aria-label="Plot question" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. is the validation loss diverging from training?" />
          <button className="btn primary" disabled={ai.isPending}>Suggest plots</button>
        </form>
        {ai.data?.note && <div className="note" style={{ marginTop: 8 }}>{ai.data.note}</div>}
        {ai.data?.rejected.map((r, i) => <div key={i} className="note warn" style={{ marginTop: 6 }}><XCircle size={14} /> rejected: {r.errors.join("; ")}</div>)}
      </Card>
      {others.length > 0 && <div className="note" data-testid="plots-overlay">Each plot shows {[runId, ...others].filter((r) => !hidden.includes(r)).map(short).join(", ")} — one colour per metric{baseline && !hidden.includes(baseline) ? ", the baseline dashed" : ""}. Hover a chart to read every curve at that step.</div>}
      {aiSpecs.map((s) => <PlotView key={`ai-${s.id}`} spec={visible(overlay(s, runId, others, baseline, has, stageOf), hidden)} baseline={baseline} />)}
      <div className="grid2">{sug.data.specs.map((s) => <PlotView key={s.id} spec={visible(overlay(s, runId, others, baseline, has, stageOf), hidden)} baseline={baseline} />)}</div>
      {sug.data.specs.length === 0 && <div className="muted" data-testid="plots-empty">{sug.data.note || "No metric series to plot yet."}</div>}
      <Card title="Plot spec editor" sub="Paste a PlotSpec v1 (JSON) — the schema is at /api/v1/plots/schema">
        <textarea className="input mono" rows={8} aria-label="Plot spec" value={text} onChange={(e) => setText(e.target.value)}
          placeholder={JSON.stringify(sug.data.specs[0] ?? { id: "my-plot", title: "My plot", series: [{ run: runId, key: "val/loss" }] }, null, 1)} />
        <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => void validate()}>Validate &amp; draw</button></div>
        {check && (check.ok
          ? <div className="note" data-testid="plot-ok"><CheckCircle2 size={14} /> valid{check.warnings.length ? ` — ${check.warnings.join("; ")}` : ""}</div>
          : <div className="note warn" data-testid="plot-errors"><XCircle size={14} /> <div>{check.errors.map((e) => <div key={e} className="mono" style={{ fontSize: 12 }}>{e}</div>)}</div></div>)}
      </Card>
      {custom && <PlotView spec={custom} />}
    </div>
  );
}
