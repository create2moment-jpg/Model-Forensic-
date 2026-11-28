/** Files → Log insights: everything the run's logs say, as a board — problems first (counted, first and last
 *  occurrence, what usually fixes it), facts about the run, events, curves the logs print — and the lines no pattern
 *  explains yet, with two ways to teach it more: proposals from the local LLM (each must match a line it cites) and a
 *  brief for the coding agent in the training project (patterns read from the code). Refreshes while the run runs;
 *  only what the logs gained is read. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, Bot, Check, ClipboardCopy, Code2, FileSearch, Info, ScrollText, X } from "lucide-react";
import { Fragment, useState } from "react";
import { api } from "../../api/client";
import type { LogEvent, LogPatternDto } from "../../api/types";
import { useApp } from "../../components/AppState";
import { Chart, PALETTE } from "../../components/Chart";
import { Card, Empty, ErrorBox, Loading } from "../../components/ui";

const SEV = { problem: <AlertOctagon size={15} color="var(--bad)" />, attention: <AlertTriangle size={15} color="var(--warn)" />, info: <Info size={15} color="var(--info)" /> };
const SOURCE: Record<string, string> = { builtin: "built in", code: "from the code", llm: "proposed", user: "yours" };
const CATS = ["environment", "data", "evaluation", "progress", "checkpoint"];

export type OpenLog = (fid: string, line: number, text: string) => void;

/** Where a line is: file:line — a link that opens the log at that line. */
function At({ ev, open }: { ev: LogEvent | null; open?: OpenLog }) {
  if (!ev) return null;
  const where = `${ev.file}:${ev.line}`;
  return (
    <div className="mono faint ellipsis" style={{ fontSize: 11.5 }} title={`${where}\n${ev.text}`}>
      {open && ev.fid ? <button type="button" className="link-btn" data-testid="open-log-line" onClick={() => open(ev.fid!, ev.line, ev.text)} title="Open the log at this line">{where}</button> : where} · {ev.text}
    </div>
  );
}

function Proposals({ runId, stage, onChange }: { runId: string; stage: string; onChange: () => void }) {
  const { toast } = useApp();
  const qc = useQueryClient();
  const pats = useQuery({ queryKey: ["log-patterns"], queryFn: api.logPatterns });
  const decide = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "active" | "rejected" }) => api.logPatternDecide(id, status),
    onSuccess: (p) => { toast(p.status === "active" ? `“${p.title}” now reads every run's logs` : `Rejected “${p.title}”`); void qc.invalidateQueries({ queryKey: ["log-patterns"] }); onChange(); },
  });
  const propose = useMutation({
    mutationFn: () => api.logPatternsPropose(runId, stage || null),
    onSuccess: (r) => { toast(r.proposed.length ? `${r.proposed.length} pattern(s) proposed by ${r.model}` : r.note ?? "Nothing new to propose", r.proposed.length ? undefined : "bad"); void qc.invalidateQueries({ queryKey: ["log-patterns"] }); },
    onError: (e) => toast(String(e), "bad"),
  });
  const brief = useMutation({
    mutationFn: async () => (await fetch(api.harnessBriefUrl(runId, "log_patterns"), { credentials: "same-origin" })).text(),
    onSuccess: (t) => { void navigator.clipboard?.writeText(t); toast("Brief copied — give it to the coding agent in your training project"); },
  });
  const proposed = (pats.data?.patterns ?? []).filter((p) => p.status === "proposed");
  return (
    <>
      <div className="row wrap" style={{ gap: 8 }}>
        <button className="btn sm primary" disabled={propose.isPending} onClick={() => propose.mutate()} data-testid="propose-patterns"><Bot size={13} /> {propose.isPending ? "Proposing…" : "Propose patterns"}</button>
        <button className="btn sm" disabled={brief.isPending} onClick={() => brief.mutate()} title="A brief for the coding agent inside your training project: it reads the code's print / log calls and writes patterns"><Code2 size={13} /> Brief for your coding agent</button>
        <span className="faint" style={{ fontSize: 12 }}>The local LLM proposes from these lines; your coding agent reads the code — both land below as proposals.</span>
      </div>
      {propose.data && propose.data.rejected.length > 0 && <div className="faint" style={{ fontSize: 12 }}>{propose.data.rejected.length} suggestion(s) dropped: {propose.data.rejected.map((r) => r.reason).join(" · ")}</div>}
      {proposed.length > 0 && (
        <div className="col" style={{ gap: 6 }} data-testid="pattern-proposals">
          <b style={{ fontSize: 13 }}>Proposed — not applied until you accept</b>
          {proposed.map((p: LogPatternDto) => (
            <div key={p.id} className="proposal-row">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="row" style={{ gap: 6 }}><b>{p.title}</b><span className="pill outline">{p.kind}</span><span className="faint" style={{ fontSize: 12 }}>{SOURCE[p.source]}{p.code_ref ? ` · ${p.code_ref}` : ""}</span></div>
                <div className="mono faint ellipsis" style={{ fontSize: 11.5 }} title={p.regex}>{p.regex}</div>
                {p.example && <div className="mono ellipsis" style={{ fontSize: 11.5 }} title={p.example}>e.g. {p.example}</div>}
              </div>
              <button className="btn sm" aria-label={`Accept ${p.title}`} onClick={() => decide.mutate({ id: p.id, status: "active" })}><Check size={13} /> Accept</button>
              <button className="btn sm ghost" aria-label={`Reject ${p.title}`} onClick={() => decide.mutate({ id: p.id, status: "rejected" })}><X size={13} /></button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

export function LogInsightsTab({ runId, stage = "", onOpenLogs, onOpenLog }: { runId: string; stage?: string; onOpenLogs?: () => void; onOpenLog?: OpenLog }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["log-insights", runId, stage], queryFn: () => api.logInsights(runId, stage || null),
    refetchInterval: (x) => (x.state.data && !["completed", "failed", "canceled"].includes(x.state.data.status) ? 30_000 : false) });
  const [showAll, setShowAll] = useState(false);
  if (q.isLoading) return <Loading label="Reading the logs…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  if (d.state === "no_logs") return <Card><Empty icon={<ScrollText size={24} />} title="No logs for this run">Log insights reads the run's log files once they exist.</Empty></Card>;
  const refresh = (): void => { void qc.invalidateQueries({ queryKey: ["log-insights", runId] }); };
  const factsBy = CATS.map((c) => [c, d.facts.filter((f) => f.category === c)] as const).filter(([, f]) => f.length);
  const pct = d.coverage.lines ? Math.round((100 * d.coverage.matched) / d.coverage.lines) : 0;
  return (
    <div className="col" style={{ gap: 14 }} data-testid="log-insights">
      <div className="row wrap faint" style={{ fontSize: 12.5, gap: 6 }}>
        <FileSearch size={13} /> {d.coverage.lines.toLocaleString()} lines in {d.files.length} log file{d.files.length === 1 ? "" : "s"} · {d.coverage.matched.toLocaleString()} explained ({pct}%) by {d.patterns.active} patterns
        ({d.patterns.builtin} built in{d.patterns.code ? `, ${d.patterns.code} from the code` : ""}{d.patterns.llm ? `, ${d.patterns.llm} proposed and accepted` : ""}{d.patterns.user ? `, ${d.patterns.user} yours` : ""})
        {!["completed", "failed", "canceled"].includes(d.status) && <span> · refreshes every 30 s (only new lines are read)</span>}
        {d.files.filter((f) => f.note).map((f) => <span key={f.id}> · {f.path}: {f.note}</span>)}
        <span className="spacer" />{onOpenLogs && <button className="btn ghost sm" onClick={onOpenLogs}><ScrollText size={13} /> Open the logs</button>}
      </div>

      <Card title="Problems" sub={d.problems.length ? (() => { const n = d.problems.reduce((m, p) => m + p.count, 0); return `${n} line${n === 1 ? "" : "s"}`; })() : "none found"} flush>
        {d.problems.length === 0 ? <div className="card-body muted">No errors or warnings in the logs.</div> : (
          <div data-testid="log-problems">
            {d.problems.map((p) => (
              <div key={p.pattern} className="log-problem">
                {SEV[p.severity]}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="row" style={{ gap: 6 }}><b>{p.title}</b><span className="faint">×{p.count}</span>{p.source !== "builtin" && <span className="faint" style={{ fontSize: 11.5 }}>{SOURCE[p.source]}</span>}</div>
                  {p.explain && <div className="muted" style={{ fontSize: 12.5 }}>{p.explain}</div>}
                  <At ev={p.first} open={onOpenLog} />
                  {p.count > 1 && p.last && p.last.line !== p.first?.line && <At ev={p.last} open={onOpenLog} />}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {factsBy.length > 0 && (
        <Card title="What the logs say about the run" flush>
          <div className="log-facts" data-testid="log-facts">
            {factsBy.map(([cat, fs]) => (
              <div key={cat}>
                <div className="sub-h">{cat}</div>
                <dl className="kv">{fs.map((f) => <Fragment key={`${f.pattern}:${f.field}`}><dt title={f.title}>{f.field}</dt><dd className="mono" title={`${f.file}:${f.line}`}>{f.value}</dd></Fragment>)}</dl>
              </div>
            ))}
          </div>
        </Card>
      )}

      {d.events.length > 0 && (
        <Card title="Events" flush>
          <div data-testid="log-events">
            {d.events.map((e) => (
              <div key={e.pattern} className="log-problem"><Info size={14} color="var(--text-3)" />
                <div style={{ minWidth: 0, flex: 1 }}><div><b>{e.title}</b> <span className="faint">×{e.count}</span></div>{e.recent.slice(-3).map((ev) => <At key={`${ev.file}:${ev.line}`} ev={ev} open={onOpenLog} />)}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {d.series.length > 0 && (
        <div className="grid g2">
          {(showAll ? d.series : d.series.slice(0, 4)).map((s, i) => (
            <Card key={s.key} title={s.label} sub={`${s.title} · over ${s.x_label}`} flush>
              <div style={{ padding: "4px 8px 8px" }}><Chart series={[{ name: s.label, x: s.x, y: s.y, color: PALETTE[i % PALETTE.length] }]} height={180} xLabel={s.x_label} showRaw={false} /></div>
            </Card>
          ))}
          {d.series.length > 4 && !showAll && <button className="btn sm" onClick={() => setShowAll(true)}>Show all {d.series.length} curves</button>}
        </div>
      )}

      <Card title="Lines no pattern explains" sub={d.uncovered.length ? `${d.uncovered.length} kinds` : "every noteworthy line is explained"} flush>
        <div className="card-body col" style={{ gap: 10 }}>
          <Proposals runId={runId} stage={stage} onChange={refresh} />
          {d.uncovered.length > 0 && (
            <div className="table-wrap" style={{ maxHeight: 320 }}>
              <table className="t" data-testid="log-uncovered">
                <thead><tr><th className="r">×</th><th>Line (example)</th><th>Where</th></tr></thead>
                <tbody>
                  {d.uncovered.map((u) => (
                    <tr key={u.template}><td className="r num">{u.count}</td><td className="mono selectable" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{u.noteworthy && <AlertTriangle size={12} color="var(--warn)" style={{ marginRight: 4, verticalAlign: -2 }} />}{u.example}</td><td className="faint mono ellipsis" style={{ maxWidth: 220, fontSize: 11.5 }}>{onOpenLog && u.fid ? <button type="button" className="link-btn" onClick={() => onOpenLog(u.fid!, u.line, u.example)}>{u.file}:{u.line}</button> : `${u.file}:${u.line}`}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="faint" style={{ fontSize: 12 }}><ClipboardCopy size={11} style={{ verticalAlign: -1 }} /> CLI: <code>mf logs insights --run {runId}</code> · <code>mf logs propose --run {runId}</code> · <code>mf harness brief --run {runId} --task log_patterns</code></div>
        </div>
      </Card>
    </div>
  );
}
