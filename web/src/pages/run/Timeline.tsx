import { useQuery } from "@tanstack/react-query";
import { FileDiff, GitBranch, Terminal } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import type { RunStatus, RunTimeline, TimelineSpan, TimelineStage } from "../../api/types";
import { Card, ErrorBox, Loading, Pill, StatusPill } from "../../components/ui";
import { fmtDuration } from "../../lib/format";

const ROLE_COLOR: Record<string, string> = {
  setup: "#8a8f98", data: "#c58b2b", training: "#4f7cff", evaluation: "#2fb37e", export: "#9b6bdf", analysis: "#1fb5c9", other: "#7a8699",
};
const secs = (s: number | null | undefined): string => (s === null || s === undefined ? "—" : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const t = (iso: string | null): number | null => (iso ? new Date(iso).getTime() : null);

/** The run as a forensic timeline: what ran when, for how long (measured or estimated), and what evidence each stage
 *  has — configs, metrics, files, logs and the code diff. */
export function TimelineTab({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["timeline", runId], queryFn: () => api.timeline(runId) });
  const [diffStage, setDiffStage] = useState<string | null | undefined>(undefined);
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  const rows: { label: string; role: string; span: TimelineSpan; sub?: boolean; status?: RunStatus }[] = d.stages.length
    ? d.stages.flatMap((s) => [{ label: s.name, role: s.role, span: s, status: s.status }, ...s.sub_stages.map((x) => ({ label: x.name, role: x.name.includes("eval") ? "evaluation" : x.name.includes("train") ? "training" : "setup", span: x, sub: true }))])
    : d.sub_stages.map((x) => ({ label: x.name, role: x.name.includes("eval") ? "evaluation" : x.name.includes("train") ? "training" : "setup", span: x }));
  const times = rows.flatMap((r) => [t(r.span.start), t(r.span.end)]).filter((x): x is number => x !== null);
  const t0 = Math.min(...times, t(d.run.started) ?? Infinity, t(d.run.created) ?? Infinity);
  const t1 = Math.max(...times, t(d.run.ended) ?? -Infinity);
  const width = t1 > t0 ? t1 - t0 : 1;
  return (
    <div className="col" style={{ gap: 14 }}>
      <RunFacts d={d} />
      <Card title="Timeline" sub="Solid: measured by the backend · striped: estimated from metric timestamps (clamped to the measured window)">
        {rows.length === 0 ? <div className="muted">No timestamps to place this run on a timeline.</div> : (
          <div className="tl">
            {rows.map((r, i) => {
              const a = t(r.span.start), b = t(r.span.end);
              const left = a !== null ? ((a - t0) / width) * 100 : 0;
              const w = a !== null && b !== null ? Math.max(0.6, ((b - a) / width) * 100) : 0;
              return (
                <div key={i} className={`tl-row ${r.sub ? "sub" : ""}`}>
                  <div className="tl-label" title={r.span.source}>{r.sub ? "↳ " : ""}{r.label}{r.status && <StatusPill status={r.status} />}</div>
                  <div className="tl-track">
                    {a !== null && <div className={`tl-bar ${r.span.estimated ? "est" : ""}`} style={{ left: `${left}%`, width: `${w}%`, background: ROLE_COLOR[r.role] ?? ROLE_COLOR.other }} title={`${r.span.source}${r.span.note ? ` — ${r.span.note}` : ""}`} />}
                  </div>
                  <div className="tl-dur" title={r.span.note}>{r.span.start === null && r.span.note ? <span className="faint">not placed</span> : <>{secs(r.span.duration_s)}{r.span.estimated && <span className="faint"> est.</span>}</>}</div>
                </div>
              );
            })}
            <div className="tl-axis"><span>{new Date(t0).toLocaleString()}</span><span>{new Date(t1).toLocaleString()}</span></div>
          </div>
        )}
      </Card>
      {d.stages.map((s) => <StageCard key={s.name} s={s} onDiff={() => setDiffStage(s.name)} />)}
      {!d.stages.length && (
        <Card title="Evidence">
          <EvidenceChips e={d.evidence} onDiff={() => setDiffStage(null)} />
        </Card>
      )}
      <CodeDiffCard runId={runId} stage={diffStage} available={d.code_diffs.length > 0} />
    </div>
  );
}

function RunFacts({ d }: { d: RunTimeline }) {
  const r = d.run;
  return (
    <Card>
      <div className="facts">
        <Fact k="Run ID" v={<span className="mono">{r.id}</span>} />
        <Fact k="Name" v={r.name} />
        <Fact k="Experiment" v={r.experiment ?? "—"} />
        <Fact k="Owner" v={r.user ?? "—"} />
        <Fact k="Status" v={<StatusPill status={r.status} />} />
        <Fact k="Current stage" v={r.current_stage ? <Pill tone="accent">{r.current_stage}</Pill> : r.status === "running" ? "—" : "finished"} />
        <Fact k="Duration" v={fmtDuration(r.started, r.ended) || secs(r.duration_s)} />
        <Fact k="Started → ended" v={`${r.started ? new Date(r.started).toLocaleString() : "—"} → ${r.ended ? new Date(r.ended).toLocaleString() : "…"}`} />
        {r.project && <Fact k="Project" v={r.project} />}
        {r.compute && <Fact k="Compute" v={r.compute} />}
        {r.environment && <Fact k="Environment" v={<span className="mono">{r.environment}</span>} />}
      </div>
      {r.command && <div className="cmd"><Terminal size={13} /><code>{r.command}</code></div>}
    </Card>
  );
}

const Fact = ({ k, v }: { k: string; v: React.ReactNode }) => <div className="fact"><div className="faint">{k}</div><div>{v}</div></div>;

function EvidenceChips({ e, onDiff }: { e: RunTimeline["evidence"]; onDiff: () => void }) {
  return (
    <div className="row wrap" style={{ gap: 6 }}>
      <Pill tone="outline">{e.configs} config{e.configs === 1 ? "" : "s"}</Pill>
      {e.metrics !== null && <Pill tone="outline">{e.metrics} metric series</Pill>}
      <Pill tone="outline">{e.files} files</Pill>
      <Pill tone={e.logs ? "outline" : "warn"}>{e.logs ? `${e.logs} log files` : "no logs"}</Pill>
      {e.code_diff ? <button className="btn sm" onClick={onDiff}><FileDiff size={13} /> code diff</button> : <Pill tone="outline">no code diff</Pill>}
    </div>
  );
}

function StageCard({ s, onDiff }: { s: TimelineStage; onDiff: () => void }) {
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><span className="role-dot" style={{ background: ROLE_COLOR[s.role] ?? ROLE_COLOR.other }} />{s.name} <Pill tone="outline">{s.role}</Pill> <StatusPill status={s.status} /></span>}
      sub={`${secs(s.duration_s)} · ${s.source}`}>
      <div className="col" style={{ gap: 8 }}>
        <EvidenceChips e={s.evidence} onDiff={onDiff} />
        {s.sub_stages.length > 0 && (
          <div className="row wrap faint" style={{ gap: 10, fontSize: 12 }}>
            {s.sub_stages.map((x) => <span key={x.name}>{x.name}: {secs(x.duration_s)}{x.estimated ? " (est.)" : ""}{x.note ? " ⚠" : ""}</span>)}
          </div>
        )}
        {s.failure && <div className="note warn">{s.failure}</div>}
        {(s.command || s.environment) && (
          <div className="faint mono" style={{ fontSize: 12 }}>
            {s.environment && <div><GitBranch size={11} /> {s.environment}{s.compute ? ` · ${s.compute}` : ""}</div>}
            {s.command && <div className="cmd"><Terminal size={12} /><code>{s.command}</code></div>}
          </div>
        )}
      </div>
    </Card>
  );
}

function CodeDiffCard({ runId, stage, available }: { runId: string; stage: string | null | undefined; available: boolean }) {
  const q = useQuery({ queryKey: ["code-diff", runId, stage ?? ""], queryFn: () => api.codeDiff(runId, stage), enabled: available });
  if (!available) {
    return <Card title={<span className="row"><FileDiff size={15} /> Code diff</span>}><div className="muted">The job saved no code diff (looked for <code>code/*repo_diff*.txt</code> — a project profile can point elsewhere).</div></Card>;
  }
  if (q.isLoading) return <Loading />;
  const d = q.data;
  if (!d || !d.available) return null;
  return (
    <Card title={<span className="row"><FileDiff size={15} /> Code diff</span>} sub={`${d.label} · ${d.stage ? `${d.stage} / ` : ""}${d.path}`}
      right={d.stats && <span className="faint" style={{ fontSize: 12 }}>{d.stats.files} file(s) · <span className="good-text">+{d.stats.additions}</span> <span className="bad">−{d.stats.deletions}</span></span>}>
      <pre className="diff">{(d.text ?? "").split("\n").map((line, i) => (
        <div key={i} className={line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : line.startsWith("diff ") ? "file" : ""}>{line || " "}</div>
      ))}</pre>
      {d.truncated && <div className="faint">… truncated at 2 MB</div>}
    </Card>
  );
}
