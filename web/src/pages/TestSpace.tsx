import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FlaskConical, Gauge, Loader2, Play, ScrollText, X } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Pct, TsExperiment, TsHypothesis, TsProposal } from "../api/types";
import { useApp } from "../components/AppState";
import { Card, Empty, ErrorBox, Loading, Pill } from "../components/ui";
import { fmtAgo } from "../lib/format";

const KINDS = ["run.stages", "run.configs", "run.artifacts", "run.metrics", "run.summary", "run.comparison", "run.code_diff"];
const COST_TONE: Record<string, string> = { lightweight: "good", medium: "outline", heavy: "warn", very_heavy: "bad" };
const ms = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : v < 1000 ? `${v.toFixed(v < 10 ? 1 : 0)} ms` : `${(v / 1000).toFixed(2)} s`);
const P = ({ p }: { p: Pct }) => <span className="num">{p.n ? <>{ms(p.p50)} · {ms(p.p90)} · {ms(p.p99)} <span className="faint">n={p.n}</span></> : <span className="faint">no samples</span>}</span>;

/** Test Space: measure task costs on sample runs (cold / warm, p50 / p90 / p99), test hypotheses, and turn the
 *  evidence into versioned proposals that change the master configuration only when someone accepts them. */
export function TestSpacePage() {
  const [sp, setSp] = useSearchParams();
  const home = useQuery({ queryKey: ["test-space"], queryFn: api.testSpace, refetchInterval: (q) => ((q.state.data?.experiments ?? []).some((e) => e.state === "running") ? 1500 : 15_000) });
  if (home.isLoading) return <div className="page"><Loading /></div>;
  if (home.error || !home.data) return <div className="page"><ErrorBox error={home.error} /></div>;
  const h = home.data;
  const sel = sp.get("exp");
  return (
    <div className="page" style={{ maxWidth: 1400 }}>
      <div className="page-head"><div><h1 className="row"><FlaskConical size={20} /> Test Space</h1>
        <div className="sub">Measure what background tasks cost on your runs — cold (caches dropped) and warm — test hypotheses, and propose reviewed changes to the task configuration. Production settings never change from one noisy measurement.</div></div></div>
      <div className="col" style={{ gap: 14 }}>
        <Card title={<span className="row"><Gauge size={15} /> Production timings</span>} sub="Measured by the application per task kind — p50 · p90 · p99" flush>
          <div className="table-wrap">
            <table className="t" data-testid="ts-production">
              <thead><tr><th>Task</th><th>Configured</th><th>Measured (cold p90)</th><th>Cold</th><th>Warm</th><th>Light Mode</th><th>Why</th></tr></thead>
              <tbody>{h.production.map((p) => (
                <tr key={p.kind}>
                  <td><b>{p.title}</b> <span className="mono faint">{p.kind}</span></td>
                  <td><Pill tone={COST_TONE[p.cost] ?? "outline"}>{p.cost.replace("_", " ")}</Pill></td>
                  <td>{p.measured_class ? <Pill tone={p.measured_class === p.cost ? "outline" : "warn"}>{p.measured_class.replace("_", " ")}</Pill> : <span className="faint">—</span>}</td>
                  <td><P p={p.cold} /></td><td><P p={p.warm} /></td>
                  <td>{p.light_mode ? "yes" : "—"}</td>
                  <td className="wrap faint" style={{ maxWidth: 360, fontSize: 12 }}>{p.why}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
        <NewExperiment defaults={h.settings.sample_runs} reps={h.settings.repetitions} onStarted={(id) => setSp({ exp: id })} />
        <div className="prefetch-layout">
          <div className="col" style={{ gap: 6, minWidth: 0 }}>
            {h.experiments.length === 0 && <Card><Empty icon={<FlaskConical size={24} />} title="No experiments yet">Start one above.</Empty></Card>}
            {h.experiments.map((e) => (
              <button key={e.id} className={`card prefetch-row ${sel === e.id ? "on" : ""}`} onClick={() => setSp({ exp: e.id })} data-state={e.state}>
                <div className="row" style={{ gap: 6 }}><b className="mono" style={{ flex: 1, textAlign: "left" }}>{e.kind}</b>{e.state === "running" ? <Loader2 size={13} className="spin" /> : <Pill tone={e.verdict === "supported" ? "good" : e.verdict === "refuted" ? "bad" : "outline"}>{e.verdict ?? e.measured_class ?? e.state}</Pill>}</div>
                <div className="row faint" style={{ fontSize: 12, gap: 8 }}><span>{e.runs} run(s) · {e.samples} samples</span><span className="spacer" />{fmtAgo(e.created_at)}</div>
              </button>
            ))}
          </div>
          <div style={{ minWidth: 0 }}>{sel && <ExperimentDetail id={sel} />}</div>
        </div>
        <Proposals items={h.proposals} />
      </div>
    </div>
  );
}

function NewExperiment({ defaults, reps, onStarted }: { defaults: string[]; reps: number; onStarted: (id: string) => void }) {
  const qc = useQueryClient();
  const { toast } = useApp();
  const [kind, setKind] = useState("run.metrics");
  const [runs, setRuns] = useState(defaults.join("\n"));
  const [n, setN] = useState(reps);
  const [hyp, setHyp] = useState<"" | TsHypothesis["type"]>("");
  const [expect, setExpect] = useState("heavy");
  const [value, setValue] = useState(2);
  const start = useMutation({
    mutationFn: () => api.tsStart({ kind, runs: runs.split(/[\s,]+/).filter(Boolean), repetitions: n, hypothesis: hyp ? (hyp === "cost_class" ? { type: hyp, expect } : { type: hyp, value }) : null }),
    onSuccess: (e) => { onStarted(e.id); void qc.invalidateQueries({ queryKey: ["test-space"] }); },
    onError: (e) => toast(String(e), "bad"),
  });
  return (
    <Card title="New experiment" sub="One at a time, so measurements do not disturb each other. Cold drops only the sample runs' caches.">
      <div className="ts-form">
        <label>Task<select className="input" aria-label="Task kind" value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select></label>
        <label>Repetitions<input className="input" type="number" min={1} max={50} aria-label="Repetitions" value={n} onChange={(e) => setN(Number(e.target.value))} /></label>
        <label>Hypothesis<select className="input" aria-label="Hypothesis" value={hyp} onChange={(e) => setHyp(e.target.value as typeof hyp)}>
          <option value="">none</option><option value="cost_class">its cost class is…</option><option value="warm_speedup">warm is ×F faster</option><option value="under_ms">cold p90 under N ms</option>
        </select></label>
        {hyp === "cost_class" && <label>Class<select className="input" value={expect} onChange={(e) => setExpect(e.target.value)}>{["lightweight", "medium", "heavy", "very_heavy"].map((c) => <option key={c}>{c}</option>)}</select></label>}
        {(hyp === "warm_speedup" || hyp === "under_ms") && <label>{hyp === "warm_speedup" ? "Factor" : "ms"}<input className="input" type="number" value={value} onChange={(e) => setValue(Number(e.target.value))} /></label>}
        <label style={{ gridColumn: "1 / -1" }}>Sample runs (one per line)<textarea className="input mono" aria-label="Sample runs" rows={3} value={runs} onChange={(e) => setRuns(e.target.value)} placeholder="workspace:run-id" /></label>
      </div>
      <button className="btn primary" disabled={start.isPending || !runs.trim()} onClick={() => start.mutate()}><Play size={14} /> Run experiment</button>
    </Card>
  );
}

function ExperimentDetail({ id }: { id: string }) {
  const qc = useQueryClient();
  const { toast } = useApp();
  const q = useQuery({ queryKey: ["ts-exp", id], queryFn: () => api.tsExperiment(id), refetchInterval: (qq) => (qq.state.data?.state === "running" ? 800 : false) });
  const propose = useMutation({ mutationFn: () => api.tsPropose(id), onSuccess: (p) => { toast(`Proposal ${p.id} created`); void qc.invalidateQueries({ queryKey: ["test-space"] }); }, onError: (e) => toast(String(e), "bad") });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const e: TsExperiment = q.data;
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><span className="mono">{e.kind}</span> <Pill tone={e.state === "done" ? "good" : e.state === "failed" ? "bad" : "accent"}>{e.state}</Pill></span>}
      sub={`${e.runs.length} run(s) × ${e.repetitions} repetition(s) · ${e.environment.join(", ")}`}
      right={e.state === "done" && <button className="btn sm" onClick={() => propose.mutate()}><ScrollText size={13} /> Propose a change</button>}>
      <div data-testid="ts-experiment" className="col" style={{ gap: 8 }}>
        {e.state === "running" && <div className="row faint" style={{ gap: 6 }}><Loader2 size={13} className="spin" /> {String(e.task?.progress.step ?? "starting")} · {e.samples.length} sample(s) so far</div>}
        {e.stats && (
          <div className="pf-stats">
            <div className="pf-stat"><div className="faint">cold p50 · p90 · p99</div><P p={e.stats.cold} /></div>
            <div className="pf-stat"><div className="faint">warm p50 · p90 · p99</div><P p={e.stats.warm} /></div>
            <div className="pf-stat"><div className="faint">measured class</div>{e.measured_class ? <Pill tone={COST_TONE[e.measured_class] ?? "outline"}>{e.measured_class}</Pill> : "—"}</div>
            <div className="pf-stat"><div className="faint">Light Mode eligible</div><b>{e.light_mode_eligible ? "yes" : "no"}</b></div>
          </div>
        )}
        {e.verdict && <div className={`note ${e.verdict.result === "refuted" ? "warn" : ""}`} data-testid="ts-verdict"><b>Hypothesis {e.verdict.result}</b> — {e.verdict.why}</div>}
        {!!e.errors && <div className="note warn">{e.errors} sample(s) failed and are left out of the statistics</div>}
        <details><summary className="faint">{e.samples.length} raw samples</summary>
          <table className="t" style={{ fontSize: 12 }}><tbody>{e.samples.map((s, i) => <tr key={i}><td>{s.rep}</td><td>{s.mode}</td><td className="mono">{s.run}</td><td className="r num">{ms(s.ms)}</td><td className="bad">{s.error}</td></tr>)}</tbody></table>
        </details>
      </div>
    </Card>
  );
}

function Proposals({ items }: { items: TsProposal[] }) {
  const qc = useQueryClient();
  const { toast } = useApp();
  const review = useMutation({ mutationFn: (a: { id: string; d: "accept" | "reject" }) => api.tsReview(a.id, a.d), onSuccess: (p) => { toast(`Proposal ${p.status}`); void qc.invalidateQueries({ queryKey: ["test-space"] }); }, onError: (e) => toast(String(e), "bad") });
  return (
    <Card title={<span className="row"><ScrollText size={15} /> Proposals</span>} sub="Versioned files in config/test-space/proposals/ · accepting writes config/tasks.local.yaml and reloads the task costs">
      {items.length === 0 ? <div className="muted">No proposals yet — finish an experiment whose measurements disagree with the configuration, then <b>Propose a change</b>.</div> : (
        <div className="col" style={{ gap: 10 }} data-testid="ts-proposals">
          {[...items].reverse().map((p) => (
            <div key={p.id} className="card card-body" data-status={p.status}>
              <div className="row" style={{ gap: 8 }}>
                <b className="mono">{p.id}</b><Pill tone={p.status === "accepted" ? "good" : p.status === "rejected" ? "outline" : "accent"}>{p.status}</Pill>
                <span className="spacer" />
                {p.status === "proposed" && <>
                  <button className="btn sm primary" onClick={() => review.mutate({ id: p.id, d: "accept" })}><Check size={13} /> Accept</button>
                  <button className="btn sm ghost" onClick={() => review.mutate({ id: p.id, d: "reject" })}><X size={13} /> Reject</button>
                </>}
              </div>
              <div style={{ marginTop: 4 }}>{Object.entries(p.changes).map(([k, c]) => <div key={k}><span className="mono">{k}</span>: <s className="faint">{String(c.current)}</s> → <b>{String(c.proposed)}</b></div>)}</div>
              <div className="faint" style={{ fontSize: 12 }}>{p.rationale} · runs {p.evidence.runs.join(", ")} · by {p.created_by} {fmtAgo(p.created_at)}{p.review ? ` · ${p.status} by ${p.review.by} ${fmtAgo(p.review.at)}` : ""}</div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
