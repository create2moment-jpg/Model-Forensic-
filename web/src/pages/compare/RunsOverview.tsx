import { useQuery } from "@tanstack/react-query";
import { Copy, GitBranch } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Run, RunLight } from "../../api/types";
import { useApp } from "../../components/AppState";
import { Card, Loading, Pill, StatusPill } from "../../components/ui";
import { fmtAgo, fmtDuration, fmtNum, fmtPct, shortSha } from "../../lib/format";

type Row = { label: string; get: (r: Run) => string; render?: (r: Run, other: Run) => React.ReactNode; strong?: boolean; always?: boolean };

const branch = (r: Run): string => (r.git.branch ? `${r.git.branch}${r.git.commit ? ` @ ${shortSha(r.git.commit)}` : ""}${r.git.dirty ? " (dirty)" : ""}` : r.git.commit ? shortSha(r.git.commit) : "—");
const compute = (r: Run): string => [r.compute.target, r.compute.gpu_count ? `${r.compute.gpu_count}×${r.compute.gpu_type ?? "GPU"}` : null, r.compute.node_count && r.compute.node_count > 1 ? `${r.compute.node_count} nodes` : null].filter(Boolean).join(" · ") || "—";

const ROWS: Row[] = [
  { label: "Run", get: (r) => r.display_name ?? r.name, strong: true },
  { label: "Feature branch", get: branch, render: (r) => <span className="row" style={{ gap: 4 }}><GitBranch size={12} className="faint" /><span className="mono">{branch(r)}</span></span> },
  { label: "Status", get: (r) => r.status, render: (r) => <StatusPill status={r.status} /> },
  { label: "Experiment", get: (r) => r.experiment ?? "—" },
  { label: "Owner", get: (r) => r.user ?? "—" },
  { label: "Created", always: true, get: (r) => (r.created_at ? `${new Date(r.created_at).toLocaleString()} (${fmtAgo(r.created_at)})` : "—") },
  { label: "Duration", get: (r) => fmtDuration(r.started_at, r.ended_at) || "—" },
  { label: "Compute", get: compute },
  { label: "Kind", get: (r) => (r.children.length ? `${r.kind} · ${r.children.length} stage(s)` : r.kind) },
  { label: "Description", get: (r) => r.description ?? "—" },
];

/** Who is who before the details: the two runs side by side from cheap, indexed facts (no metric or config load),
 *  differences highlighted — name, feature branch, status, owner, timing, compute, tags, headline values. */
export function RunsOverview({ baseline, candidate }: { baseline: string; candidate: string }) {
  const b = useQuery({ queryKey: ["run-light", baseline], queryFn: () => api.runLight(baseline) });
  const c = useQuery({ queryKey: ["run-light", candidate], queryFn: () => api.runLight(candidate) });
  const { toast } = useApp();
  if (!b.data || !c.data) return <Card><Loading label="Loading the two runs…" /></Card>;
  const B = b.data.run, C = c.data.run;
  const diffs = ROWS.filter((r) => r.label !== "Run" && !r.always && r.get(B) !== r.get(C)).map((r) => r.label.toLowerCase());
  const tagKeys = [...new Set([...Object.keys(B.tags), ...Object.keys(C.tags)])].filter((k) => B.tags[k] !== C.tags[k]).sort();
  const heads = headline(b.data, c.data);
  const cell = (r: Run, light: RunLight, id: string, role: string) => (
    <th style={{ textTransform: "none", verticalAlign: "top" }}>
      <div className="faint" style={{ fontSize: 11 }}>{role}</div>
      <Link to={`/runs/${encodeURIComponent(id)}`} style={{ fontWeight: 600, fontSize: 14 }}>{r.display_name ?? r.name}</Link>
      <div className="row mono faint" style={{ gap: 4, fontSize: 11.5 }}>{light.id}
        <button className="icon-btn" aria-label={`Copy ${role} run id`} title="Copy run id" onClick={() => { void navigator.clipboard?.writeText(light.id); toast("Copied run id"); }}><Copy size={11} /></button>
      </div>
    </th>
  );
  return (
    <Card title="The two runs" sub={diffs.length ? `They differ in ${diffs.join(", ")}${tagKeys.length ? `, ${tagKeys.length} tag(s)` : ""}` : "Same branch, owner, experiment and compute"} flush>
      <div className="table-wrap" data-testid="runs-overview">
        <table className="t" data-nosort>
          <thead><tr><th style={{ width: 150 }} />{cell(B, b.data, baseline, "Baseline")}{cell(C, c.data, candidate, "Candidate")}</tr></thead>
          <tbody>
            {ROWS.filter((r) => r.label !== "Run").map((r) => {
              const differs = !r.always && r.get(B) !== r.get(C);  // creation time always differs: not a finding
              return (
                <tr key={r.label} className={differs ? "diff-row" : ""} data-differs={differs || undefined}>
                  <td className="faint">{r.label}{differs && <Pill tone="warn">differs</Pill>}</td>
                  <td className="wrap">{r.render ? r.render(B, C) : r.get(B)}</td>
                  <td className="wrap">{r.render ? r.render(C, B) : r.get(C)}</td>
                </tr>
              );
            })}
            {tagKeys.map((k) => (
              <tr key={`tag-${k}`} className="diff-row" data-differs>
                <td className="faint mono">tag {k}</td><td className="mono">{B.tags[k] ?? "—"}</td><td className="mono">{C.tags[k] ?? "—"}</td>
              </tr>
            ))}
            {heads.map(([k, bv, cv]) => {
              const rel = bv !== undefined && cv !== undefined && bv !== 0 ? (cv - bv) / Math.abs(bv) : null;
              return (
                <tr key={`m-${k}`}>
                  <td className="faint mono">{k}</td>
                  <td className="num">{fmtNum(bv)}</td>
                  <td className="num">{fmtNum(cv)}{rel !== null && <span className="faint"> ({rel >= 0 ? "+" : ""}{fmtPct(rel)})</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** Headline values from the index (no history load): the candidate's headline keys, plus the baseline's. */
function headline(b: RunLight, c: RunLight): [string, number | undefined, number | undefined][] {
  const keys = [...new Set([...(c.headline ?? []), ...(b.headline ?? [])])].slice(0, 6);
  return keys.map((k) => [k, b.summary_metrics[k], c.summary_metrics[k]]);
}
