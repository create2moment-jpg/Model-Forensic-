import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BookOpenCheck, CheckCircle2, Copy, Download, FileCode2, GitCompare, HeartPulse, Lightbulb, LineChart, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { api, type ExpCheck, type ExpTerm } from "../../api/client";
import type { MetricSeriesDto } from "../../api/types";
import { Chart, type ChartSeries, type HLine } from "../../components/Chart";
import { Card, Loading, Pill } from "../../components/ui";

const HYP: Record<string, { tone: string; next: string }> = {
  data: { tone: "info", next: "Look at the data: label coverage, targets present for every source, class filters, empty frames." },
  implementation: { tone: "bad", next: "Look at the code path: is the weight applied, the normaliser, the initialisation, what goes into the total." },
  config: { tone: "warn", next: "Look at the run's config against what the code needs (the key, its value, the stage it applies to)." },
  schedule: { tone: "accent", next: "Look at when things switch: warm-up, epoch-based switches, the stage config." },
  optimisation: { tone: "warn", next: "Look at the optimisation: LR, warm-up, clipping, numerical stability." },
};
const ICON = { bad: <XCircle size={15} style={{ color: "var(--bad)", flex: "none" }} />, warn: <AlertTriangle size={15} style={{ color: "var(--warn, #f5b84b)", flex: "none" }} />, ok: <CheckCircle2 size={15} style={{ color: "var(--good)", flex: "none" }} />, na: <CheckCircle2 size={15} style={{ color: "var(--text-3)", flex: "none" }} /> };

interface Finding { status: "bad" | "warn"; source: "code" | "health" | "baseline"; text: string; next: string; hyp?: string; keys: string[] }

/** The expert's review of a run: the codebase's expectations checked against the logged losses, the training health
 *  questions, and (with a baseline) how the losses progress differently — one prioritised list with the next step. */
export function ExpertReview({ runId, baseline, onPlot }: { runId: string; baseline: string | null; onPlot: (keys: string[]) => void }) {
  const exp = useQuery({ queryKey: ["expectations", runId], queryFn: () => api.expectations(runId) });
  const qs = useQuery({ queryKey: ["loss-questions", runId, baseline], queryFn: () => api.lossQuestions(runId, baseline) });
  const drift = useQuery({ queryKey: ["loss-drift", baseline, runId], queryFn: () => api.lossDrift(baseline!, runId), enabled: !!baseline && baseline !== runId });
  const findings: Finding[] = [
    ...(exp.data?.checks ?? []).filter((c) => c.status === "bad" || c.status === "warn").map((c): Finding => ({ status: c.status as "bad" | "warn", source: "code", text: c.text, next: HYP[c.hypothesis]?.next ?? "", hyp: c.hypothesis, keys: c.keys })),
    ...(qs.data?.questions ?? []).filter((q) => q.status === "bad" || q.status === "warn").map((q): Finding => ({ status: q.status as "bad" | "warn", source: "health", text: `${q.question} ${q.answer}`, next: q.ideas[0] ?? "", keys: q.keys })),
    ...(drift.data?.rows ?? []).filter((r) => r.outcome === "behind" || r.outcome === "crossed").slice(0, 3).map((r): Finding => ({ status: "warn", source: "baseline", text: `${r.key}: ${r.sentence}`, next: r.fits.length ? `Config changes that fit the timing: ${r.fits.slice(0, 2).join("; ")}` : r.lr_note, keys: [r.key] })),
  ].sort((a, b) => (a.status === b.status ? 0 : a.status === "bad" ? -1 : 1));
  const loading = exp.isLoading || qs.isLoading;
  return (
    <div className="col" style={{ gap: 14 }} data-testid="expert-review">
      <Card title={<span className="row"><BookOpenCheck size={15} /> Review</span>} fold="review.summary"
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
                  <Pill tone="outline">{f.source === "code" ? "vs code" : f.source === "health" ? "health" : "vs baseline"}</Pill>
                  {f.hyp && <Pill tone={HYP[f.hyp]?.tone ?? "outline"}>{f.hyp}</Pill>}
                  {f.keys.length > 0 && <button type="button" className="btn ghost sm icon" aria-label="Plot" title={`Plot ${f.keys.slice(0, 4).join(", ")}`} onClick={() => onPlot(f.keys.slice(0, 8))}><LineChart size={12} /></button>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>
      <CodeExpectations runId={runId} onPlot={onPlot} />
      {baseline && baseline !== runId ? (
        <Card title={<span className="row"><GitCompare size={15} /> Against the baseline</span>} fold="review.drift" sub={drift.data?.summary ?? "how the losses progress differently"}>
          {drift.isLoading ? <Loading /> : <ul className="review-plain">{(drift.data?.readings ?? []).map((r) => <li key={r}>{r}</li>)}</ul>}
        </Card>
      ) : (
        <div className="note"><GitCompare size={14} /> Pick a baseline at the top of the page to also review how the losses progress against it.</div>
      )}
      <Card title={<span className="row"><HeartPulse size={15} /> Training health</span>} fold="review.health" defaultFolded sub="all questions: Metrics tab">
        <ul className="review-plain">{(qs.data?.questions ?? []).map((q) => <li key={q.id}>{ICON[q.status]} <b>{q.question}</b> <span className="faint">{q.answer}</span></li>)}</ul>
      </Card>
    </div>
  );
}

function CodeExpectations({ runId, onPlot }: { runId: string; onPlot: (keys: string[]) => void }) {
  const q = useQuery({ queryKey: ["expectations", runId], queryFn: () => api.expectations(runId) });
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
      <td className="mono faint trunc" style={{ fontSize: 11 }} title={c.code_refs.join("\n")}>{c.code_refs.slice(0, 2).join(" ")}</td>
      <td className="act">{c.keys.length > 0 && <button type="button" className="btn ghost sm icon" aria-label={`Plot ${c.term}`} onClick={() => onPlot(c.keys.slice(0, 8))}><LineChart size={12} /></button>}</td>
    </tr>
  );
}

/** Each expected term: the logged curve with what the code expects drawn in (initial value, possible range). */
function TermCharts({ runId, terms }: { runId: string; terms: ExpTerm[] }) {
  const keys = terms.map((t) => t.key!).slice(0, 8);
  const q = useQuery({ queryKey: ["series", runId, keys, "expect"], queryFn: () => api.metrics(runId, keys, 600) });
  if (q.isLoading) return <Loading />;
  return (
    <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))", padding: "8px 14px" }}>
      {terms.slice(0, 8).map((t) => {
        const s = q.data?.find((x: MetricSeriesDto) => x.key === t.key);
        if (!s) return null;
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

