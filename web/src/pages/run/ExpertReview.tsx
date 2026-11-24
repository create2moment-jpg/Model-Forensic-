import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, BookOpenCheck, CheckCircle2, Copy, Download, FileCode2, GitCompare, HeartPulse, Lightbulb, LineChart, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { CodeRef } from "../../components/CodeRef";
import { api, type DynamicsDto, type ExpCheck, type ExpTerm } from "../../api/client";
import { Chart, type ChartSeries, type HLine } from "../../components/Chart";
import { Card, Loading, Pill } from "../../components/ui";
import { StageSelect } from "../../components/StageSelect";
import { useState } from "react";

const HYP: Record<string, { tone: string; next: string }> = {
  data: { tone: "info", next: "Look at the data: label coverage, targets present for every source, class filters, empty frames." },
  implementation: { tone: "bad", next: "Look at the code path: is the weight applied, the normaliser, the initialisation, what goes into the total." },
  config: { tone: "warn", next: "Look at the run's config against what the code needs (the key, its value, the stage it applies to)." },
  schedule: { tone: "accent", next: "Look at when things switch: warm-up, epoch-based switches, the stage config." },
  optimisation: { tone: "warn", next: "Look at the optimisation: LR, warm-up, clipping, numerical stability." },
};
const ICON = { bad: <XCircle size={15} style={{ color: "var(--bad)", flex: "none" }} />, warn: <AlertTriangle size={15} style={{ color: "var(--warn, #f5b84b)", flex: "none" }} />, ok: <CheckCircle2 size={15} style={{ color: "var(--good)", flex: "none" }} />, na: <CheckCircle2 size={15} style={{ color: "var(--text-3)", flex: "none" }} /> };

interface Finding { status: "bad" | "warn"; source: "code" | "health" | "baseline" | "dynamics"; text: string; next: string; hyp?: string; keys: string[] }

/** The expert's review of a run: the codebase's expectations checked against the logged losses, the training health
 *  questions, and (with a baseline) how the losses progress differently — one prioritised list with the next step. */
export function ExpertReview({ runId, baseline, onPlot, stage: pageStage, onStage }: { runId: string; baseline: string | null; onPlot: (keys: string[]) => void; stage?: string; onStage?: (s: string) => void }) {
  const [localStage, setLocalStage] = useState("");
  const stage = pageStage ?? localStage;  // the run page's stage (?stage=), else this view's own
  const setStage = onStage ?? setLocalStage;
  // the stage names from the pipeline's listing (never the whole pipeline's metric histories)
  const stageList = useQuery({ queryKey: ["stages", runId], queryFn: () => api.stages(runId), enabled: !runId.startsWith("group:"), staleTime: 60_000 });
  const exp = useQuery({ queryKey: ["expectations", runId, stage], queryFn: () => api.expectations(runId, stage || null) });
  const qs = useQuery({ queryKey: ["loss-questions", runId, baseline, stage], queryFn: () => api.lossQuestions(runId, baseline, stage || null) });
  const drift = useQuery({ queryKey: ["loss-drift", baseline, runId, stage], queryFn: () => api.lossDrift(baseline!, runId, stage || null), enabled: !!baseline && baseline !== runId });
  // one shape for every analysis (design review D4): problems first, from the code, health, internals, drift, findings
  const obs = useQuery({ queryKey: ["observations", runId, baseline, stage], queryFn: () => api.observations(runId, baseline, stage || null) });
  const LABEL: Record<string, Finding["source"]> = { code: "code", health: "health", internals: "health", drift: "baseline", dynamics: "dynamics", finding: "health", comparison: "baseline" };
  const findings: Finding[] = (obs.data?.observations ?? []).filter((o) => o.status === "bad" || o.status === "warn").map((o): Finding => ({
    status: o.status as "bad" | "warn", source: LABEL[o.source] ?? "health",
    text: o.source === "code" ? o.statement : `${o.title} — ${o.statement}`, next: o.next_step,
    hyp: o.cause !== "unknown" ? o.cause : undefined, keys: o.evidence,
  }));
  const loading = obs.isLoading;
  return (
    <div className="col" style={{ gap: 14 }} data-testid="expert-review">
      <Card title={<span className="row"><BookOpenCheck size={15} /> Review</span>} fold="review.summary" right={<StageSelect stages={stageList.data?.map((s) => s.name) ?? []} value={stage} onChange={setStage} />}
        sub={loading ? "reading the run…" : `${findings.filter((f) => f.status === "bad").length} problems · ${findings.filter((f) => f.status === "warn").length} to look at — against the code, training health${baseline ? " and the baseline" : ""}`}>
        {loading ? <Loading /> : findings.length === 0 ? (
          <div className="faint">Nothing stands out{exp.data?.found ? " — the losses behave as the code says" : ""}. {!exp.data?.found && "Add the codebase's expectations (below) for a deeper check."}</div>
        ) : (
          <ol className="review-list" data-testid="review-findings">
            {findings.slice(0, 12).map((f, i) => (
              <li key={i} className={f.status}>
                <div className="row" style={{ gap: 6, alignItems: "flex-start" }}>
                  {ICON[f.status]}
                  <div className="col" style={{ gap: 2, minWidth: 0 }}>
                    <span>{f.text}</span>
                    {f.next && <span className="faint" style={{ fontSize: 12 }}><Lightbulb size={11} /> {f.next}</span>}
                  </div>
                  <span className="spacer" />
                  <Pill tone="outline">{f.source === "code" ? "vs code" : f.source === "health" ? "health" : f.source === "dynamics" ? "LR schedule" : "vs baseline"}</Pill>
                  {f.hyp && <Pill tone={HYP[f.hyp]?.tone ?? "outline"}>{f.hyp}</Pill>}
                  {f.keys.length > 0 && <button type="button" className="btn ghost sm icon" aria-label="Plot" title={`Plot ${f.keys.slice(0, 4).join(", ")}`} onClick={() => onPlot(f.keys.slice(0, 8))}><LineChart size={12} /></button>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>
      <CodeExpectations runId={runId} stage={stage} onPlot={onPlot} />
      {baseline && baseline !== runId ? (
        <Card title={<span className="row"><GitCompare size={15} /> Against the baseline</span>} fold="review.drift" sub={drift.data?.summary ?? "how the losses progress differently"}>
          {drift.isLoading ? <Loading /> : <ul className="review-plain">{(drift.data?.readings ?? []).map((r) => <li key={r}>{r}</li>)}</ul>}
        </Card>
      ) : (
        <div className="note"><GitCompare size={14} /> Pick a baseline at the top of the page to also review how the losses progress against it.</div>
      )}
      <LossDynamics runId={runId} baseline={baseline} stage={stage} onPlot={onPlot} />
      <Card title={<span className="row"><HeartPulse size={15} /> Training health</span>} fold="review.health" defaultFolded sub="all questions: Metrics tab">
        <ul className="review-plain">{(qs.data?.questions ?? []).map((q) => <li key={q.id}>{ICON[q.status]} <b>{q.question}</b> <span className="faint">{q.answer}</span></li>)}</ul>
      </Card>
    </div>
  );
}

function CodeExpectations({ runId, stage, onPlot }: { runId: string; stage: string; onPlot: (keys: string[]) => void }) {
  const q = useQuery({ queryKey: ["expectations", runId, stage], queryFn: () => api.expectations(runId, stage || null) });
  if (q.isLoading) return <Card title="Against the codebase"><Loading /></Card>;
  const d = q.data!;
  if (!d.found) return <ExpectationsHowTo runId={runId} where={d.where} command={d.brief_command} errors={d.errors} />;
  const checks = d.checks ?? [];
  const terms = (d.terms ?? []).filter((t) => t.key);
  return (
    <Card title={<span className="row"><FileCode2 size={15} /> Against the codebase</span>} fold="review.code"
      sub={`${d.counts?.bad ?? 0} mismatches · ${d.counts?.warn ?? 0} to look at · ${d.counts?.ok ?? 0} as expected — ${d.codebase || "expectations"}${d.generated_at ? `, ${d.generated_at}` : ""}`} flush>
      <div data-testid="code-expectations">
        <table className="t compact" data-nosort style={{ tableLayout: "fixed", width: "100%" }}>
          <thead><tr><th style={{ width: 30 }} /><th style={{ width: 110 }}>check</th><th>finding</th><th style={{ width: 120 }}>points at</th><th style={{ width: 170 }}>code</th><th style={{ width: 36 }} /></tr></thead>
          <tbody>
            {checks.map((c) => <CheckRow key={c.id} c={c} onPlot={onPlot} />)}
          </tbody>
        </table>
        {terms.length > 0 && <TermCharts runId={runId} terms={terms} />}
        <div className="faint" style={{ fontSize: 11, padding: "6px 14px 10px" }}>From <span className="mono">{d.file}</span>{d.generated_by ? ` · by ${d.generated_by}` : ""}{d.config_source ? ` · config ${d.config_source}` : ""}{(d.notes ?? []).length ? ` · notes: ${d.notes!.join("; ")}` : ""}</div>
      </div>
    </Card>
  );
}

function CheckRow({ c, onPlot }: { c: ExpCheck; onPlot: (keys: string[]) => void }) {
  return (
    <tr data-check={c.id} data-status={c.status}>
      <td>{ICON[c.status]}</td>
      <td className="nowrap" style={{ verticalAlign: "top" }}>{c.kind}</td>
      <td style={{ whiteSpace: "normal" }}>{c.text}</td>
      <td>{c.status !== "ok" && <Pill tone={HYP[c.hypothesis]?.tone ?? "outline"}>{c.hypothesis}</Pill>}</td>
      <td className="faint trunc" style={{ fontSize: 11 }} title={c.code_refs.join("\n")}>{c.code_refs.slice(0, 2).map((r) => <span key={r} style={{ marginRight: 6 }}><CodeRef refText={r} /></span>)}</td>
      <td className="act">{c.keys.length > 0 && <button type="button" className="btn ghost sm icon" aria-label={`Plot ${c.term}`} onClick={() => onPlot(c.keys.slice(0, 8))}><LineChart size={12} /></button>}</td>
    </tr>
  );
}

/** Each expected term: the logged curve with what the code expects drawn in (initial value, possible range). */
function TermCharts({ runId, terms }: { runId: string; terms: ExpTerm[] }) {
  return (
    <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))", padding: "8px 14px" }}>
      {terms.slice(0, 8).map((t) => {
        const s = t.points;
        if (!s || s.steps.length === 0) return null;
        const series: ChartSeries[] = [{ name: t.key!, x: s.steps, y: s.values, color: "#7c6cff" }];
        const hl: HLine[] = [];
        if (t.initial && s.steps.length > 1) {  // what the code expects at the start, drawn over the first tenth (keeps it in range)
          const x0 = s.steps[0]!, x1 = s.steps[Math.max(1, Math.floor(s.steps.length / 10))]!;
          series.push({ name: `code: start ≈ ${t.initial.value}`, x: [x0, x1], y: [t.initial.value, t.initial.value], color: "#3ccf8e", dash: true, width: 2 });
        }
        if (t.range?.min !== null && t.range?.min !== undefined) hl.push({ y: t.range.min, label: `min ${t.range.min}`, color: "#ff5d73" });
        if (t.range?.max !== null && t.range?.max !== undefined) hl.push({ y: t.range.max, label: `max ${t.range.max}`, color: "#ff5d73" });
        return (
          <div key={t.name} className="card" style={{ minWidth: 0 }}>
            <Chart title={t.key!} series={series} hlines={hl} height={190} showRaw={false} smoothing={0.6} syncKey={`expect-${runId}`} />
            <div className="faint" style={{ fontSize: 11, padding: "0 10px 8px" }}>{t.formula}{t.weight !== null ? ` · weight ${t.weight}` : ""}</div>
          </div>
        );
      })}
    </div>
  );
}

function ExpectationsHowTo({ runId, where, command, errors }: { runId: string; where: string; command: string; errors: string[] }) {
  const step = (n: number, body: ReactNode) => <li><span className="step-n">{n}</span><div>{body}</div></li>;
  return (
    <Card title={<span className="row"><FileCode2 size={15} /> Against the codebase</span>} fold="review.code" sub="not set up for this run yet">
      <div data-testid="expectations-howto" className="col" style={{ gap: 10 }}>
        <div>Let the codebase speak: a coding agent (your local LLM) reads the training code for this run's config and writes what every loss should do — formula, weight, starting value, range, when it switches on. Model Forensic then checks the logged losses against it and says whether a surprise points at the data, the implementation, the config or the schedule.</div>
        <ol className="howto">
          {step(1, <>Make the brief (it lists the run's config and logged losses, and ends with the task):<div className="row" style={{ gap: 6, marginTop: 4 }}><code className="mono">{command}</code><button type="button" className="btn ghost sm icon" aria-label="Copy the command" onClick={() => void navigator.clipboard?.writeText(command)}><Copy size={12} /></button>
            <a className="btn sm" href={`/api/v1/runs/${encodeURIComponent(runId)}/harness-brief?task=loss_expectations`} download="brief.md"><Download size={12} /> brief.md</a></div></>)}
          {step(2, <>Open your local LLM / coding agent <b>inside the training repository</b> and give it <code>brief.md</code> (the task is <code>harness/tasks/loss_expectations.md</code>).</>)}
          {step(3, <>It writes the YAML to <code className="mono">{where}/</code> and checks it with <code>mf expectations --check &lt;file&gt;</code>.</>)}
          {step(4, <>Reload this tab — the checks run on the next view (also <code>mf expectations --run {runId}</code>).</>)}
        </ol>
        {errors.length > 0 && <div className="note warn">Files that could not be read: {errors.join("; ")}</div>}
      </div>
    </Card>
  );
}


const RATE_TONE: Record<string, string> = { lagging: "warn", not_learning: "bad", leading: "info", normal: "outline" };
const VERDICT: Record<string, { tone: string; label: string }> = {
  mostly_schedule: { tone: "warn", label: "mostly the schedule" }, masked: { tone: "warn", label: "schedule hides it" },
  holds: { tone: "good", label: "the change" }, same_schedule: { tone: "outline", label: "same schedule" },
  no_gap: { tone: "outline", label: "no gap" }, no_fit: { tone: "outline", label: "no fit" },
};

/** What the LR schedule did vs what the model learned (fitted L0 + A·S1^-α − C·S2), gaps to the baseline that are
 *  only the schedule, and the training rate of each loss within its group (docs/guide/learning-from-the-loss.md). */
function LossDynamics({ runId, baseline, stage, onPlot }: { runId: string; baseline: string | null; stage: string; onPlot: (keys: string[]) => void }) {
  const b = baseline && baseline !== runId ? baseline : null;
  const q = useQuery({ queryKey: ["loss-dynamics", runId, b, stage], queryFn: () => api.lossDynamics(runId, b, stage || null) });
  const d: DynamicsDto | undefined = q.data;
  const slow = (d?.rates ?? []).filter((r) => r.status !== "normal").sort((x, y) => y.rate_end - x.rate_end);
  return (
    <Card title={<span className="row"><Activity size={15} /> Loss dynamics</span>} fold="review.dynamics" defaultFolded
      sub={q.isLoading ? "fitting…" : !d?.lr_logged ? "no learning-rate series: only the training rates" : "what the LR schedule did vs what the model learned · training rate per loss"}>
      {q.isLoading || !d ? <Loading /> : (
        <div className="col" style={{ gap: 10 }} data-testid="loss-dynamics">
          {d.readings.length > 0 && <ul className="review-plain">{d.readings.map((r) => <li key={r}>{r}</li>)}</ul>}
          {d.effects.length > 0 && (
            <table className="t compact" data-nosort data-testid="dynamics-effects">
              <thead><tr><th>loss</th><th>against the baseline, on one LR schedule</th><th style={{ width: 150 }}>verdict</th></tr></thead>
              <tbody>{d.effects.map((e) => (
                <tr key={e.key}><td className="mono nowrap">{e.key}</td><td style={{ whiteSpace: "normal" }}>{e.sentence.replace(`${e.key}: `, "")}</td>
                  <td><Pill tone={VERDICT[e.verdict]?.tone ?? "outline"}>{VERDICT[e.verdict]?.label ?? e.verdict}</Pill></td></tr>
              ))}</tbody>
            </table>
          )}
          {d.fits.length > 0 && (
            <table className="t compact" data-nosort data-testid="dynamics-fits">
              <thead><tr><th>loss</th><th>R²</th><th>LR decay took off</th><th>floor</th><th>1.5× longer</th><th /></tr></thead>
              <tbody>{d.fits.map((f) => (
                <tr key={f.key} title={f.sentence}>
                  <td className="mono nowrap">{f.key}</td>
                  <td>{f.fit ? f.fit.r2.toFixed(2) : "—"}{f.fit && !f.fit.usable && <span className="faint"> (no fit)</span>}</td>
                  <td>{f.anneal_share !== undefined ? `${(f.anneal_share * 100).toFixed(0)}%` : "—"}</td>
                  <td>{f.fit?.usable ? (f.floor_identified ? f.fit.l0.toPrecision(4) : "not visible yet") : "—"}</td>
                  <td>{f.longer_1_5x != null && f.fit ? `${f.longer_1_5x.toPrecision(4)}${f.floor_identified ? "" : " (optimistic)"}` : "—"}</td>
                  <td className="act"><button type="button" className="btn ghost sm icon" aria-label={`Plot ${f.key}`} onClick={() => onPlot([f.key, "train/lr"])}><LineChart size={12} /></button></td>
                </tr>
              ))}</tbody>
            </table>
          )}
          {slow.length > 0 ? (
            <table className="t compact" data-nosort data-testid="dynamics-rates">
              <thead><tr><th>loss</th><th>group</th><th>r at the end</th><th>at % of its start</th>{b && <th>baseline r</th>}<th /></tr></thead>
              <tbody>{slow.map((r) => (
                <tr key={r.key}>
                  <td className="mono nowrap">{r.key}</td><td className="mono faint">{r.group}</td>
                  <td><Pill tone={RATE_TONE[r.status] ?? "outline"}>{r.rate_end.toFixed(2)} · {r.status.replace("_", " ")}</Pill>{r.lag_from !== null && <span className="faint"> from {(r.lag_from * 100).toFixed(0)}%</span>}</td>
                  <td>{(r.ratio_end * 100).toFixed(0)}%</td>{b && <td>{r.base_rate_end !== null ? r.base_rate_end.toFixed(2) : "—"}</td>}
                  <td className="act"><button type="button" className="btn ghost sm icon" aria-label={`Plot ${r.key}`} onClick={() => onPlot((d.rates.filter((x) => x.group === r.group).map((x) => x.key)).slice(0, 8))}><LineChart size={12} /></button></td>
                </tr>
              ))}</tbody>
            </table>
          ) : <div className="faint">Every loss trains at its group's pace (r = L(t)/L(0) over the group's mean).</div>}
          <div className="faint" style={{ fontSize: 11 }}>LR model: L(s) ≈ L0 + A·S1^−α − C·S2 (S1 summed LR, S2 annealing area; Tissue et al. 2024) — nothing is read from a fit with R² below the threshold. Training rate: GradNorm's L(t)/L(0) — a diagnosis, not a reason to re-weight.</div>
        </div>
      )}
    </Card>
  );
}
