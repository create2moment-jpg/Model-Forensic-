import { SubjectLabel } from "../../components/SubjectPicker";
import { useQueries } from "@tanstack/react-query";
import { Copy, GitBranch } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Run, RunLight } from "../../api/types";
import { useApp } from "../../components/AppState";
import { Card, ErrorBox, Loading, Pill, StatusPill } from "../../components/ui";
import { MetricName } from "../../components/MetricTile";
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

/** A value from the index under either spelling (``val/AMOTA`` from loaded histories, ``AMOTA`` from a listing). */
function valueOf(l: RunLight, k: string): number | undefined {
  const m = l.summary_metrics;
  if (m[k] !== undefined) return m[k];
  const bare = k.includes("/") ? k.slice(k.indexOf("/") + 1) : k;
  if (m[bare] !== undefined) return m[bare];
  const hit = Object.keys(m).find((x) => x.endsWith(`/${bare}`));
  return hit ? m[hit] : undefined;
}

/** Who is who before the details: the baseline and every candidate side by side from cheap, indexed facts (no metric
 *  or config load) — name, feature branch, status, owner, timing, compute, tags, headline values — with what differs
 *  from the baseline highlighted. A run group (several run ids for one model) is one column. */
export function RunsOverview({ baseline, candidates, focus = 0, onFocus }: { baseline: string; candidates: string[]; focus?: number; onFocus?: (i: number) => void }) {
  const ids = [baseline, ...candidates];
  const qs = useQueries({ queries: ids.map((id) => ({ queryKey: ["run-light", id], queryFn: () => api.runLight(id) })) });
  const { toast } = useApp();
  if (qs.some((q) => q.error)) return <Card><ErrorBox error={qs.find((q) => q.error)!.error} /></Card>;
  if (qs.some((q) => !q.data)) return <Card><Loading label={`Loading the ${ids.length} runs…`} /></Card>;
  const lights = qs.map((q) => q.data!);
  const runs = lights.map((l) => l.run);
  const B = runs[0]!;
  const cands = runs.slice(1);
  const rowDiff = (r: Row): boolean => !r.always && cands.some((c) => r.get(c) !== r.get(B));
  const diffs = ROWS.filter((r) => r.label !== "Run" && rowDiff(r)).map((r) => r.label.toLowerCase());
  const tagKeys = [...new Set(runs.flatMap((r) => Object.keys(r.tags)))].filter((k) => cands.some((c) => c.tags[k] !== B.tags[k])).sort();
  const heads = [...new Set(lights.flatMap((l) => l.headline ?? []))].slice(0, 6);
  const head = (r: Run, light: RunLight, id: string, i: number) => {
    const role = i === 0 ? "Baseline" : candidates.length > 1 ? `Candidate ${i}` : "Candidate";
    const members = light.run.children.length && id.startsWith("group:") ? light.run.children.length : 0;
    return (
      <th key={id} style={{ textTransform: "none", verticalAlign: "top" }} className={i > 0 && i - 1 === focus && candidates.length > 1 ? "focus-col" : ""}>
        <div className="faint" style={{ fontSize: 11 }}>{role}{members ? ` · ${members} runs as one` : ""}</div>
        <Link to={`/runs/${encodeURIComponent(id)}`} style={{ fontWeight: 600, fontSize: 14 }}><SubjectLabel id={id} fallback={r.display_name ?? r.name} /></Link>
        <div className="row mono faint" style={{ gap: 4, fontSize: 11.5 }}><span className="ellipsis" style={{ maxWidth: 260 }} title={light.id}>{light.id}</span>
          <button className="icon-btn" aria-label={`Copy ${role} run id`} title="Copy run id" onClick={() => { void navigator.clipboard?.writeText(light.id); toast("Copied run id"); }}><Copy size={11} /></button>
        </div>
        {i > 0 && candidates.length > 1 && onFocus && <button className="btn ghost sm" onClick={() => onFocus(i - 1)}>{i - 1 === focus ? "details below" : "show details"}</button>}
      </th>
    );
  };
  const cellCls = (differs: boolean, i: number): string => (i > 0 && differs ? "diff-cell" : "");
  return (
    <Card fold="compare.overview" title={candidates.length > 1 ? `Baseline and ${candidates.length} candidates` : "The two runs"}
      sub={diffs.length ? `They differ in ${diffs.join(", ")}${tagKeys.length ? `, ${tagKeys.length} tag(s)` : ""}` : "Same branch, owner, experiment and compute"} flush>
      <div className="table-wrap" data-testid="runs-overview">
        <table className="t" data-nosort>
          <thead><tr><th style={{ width: 150 }} />{runs.map((r, i) => head(r, lights[i]!, ids[i]!, i))}</tr></thead>
          <tbody>
            {ROWS.filter((r) => r.label !== "Run").map((row) => {
              const differs = rowDiff(row);
              return (
                <tr key={row.label} data-differs={differs || undefined}>
                  <td className="faint">{row.label}{differs && <Pill tone="warn">differs</Pill>}</td>
                  {runs.map((r, i) => <td key={ids[i]} className={`wrap ${cellCls(!row.always && row.get(r) !== row.get(B), i)}`}>{row.render ? row.render(r, B) : row.get(r)}</td>)}
                </tr>
              );
            })}
            {tagKeys.map((k) => (
              <tr key={`tag-${k}`} data-differs>
                <td className="faint mono">tag {k}</td>
                {runs.map((r, i) => <td key={ids[i]} className={`mono ${cellCls(r.tags[k] !== B.tags[k], i)}`}>{r.tags[k] ?? "—"}</td>)}
              </tr>
            ))}
            {heads.map((k) => {
              const bv = valueOf(lights[0]!, k);
              const lowerBetter = /loss|error|ate|ase|aoe|ave|aae|ece|ids|amotp/i.test(k);
              return (
                <tr key={`m-${k}`}>
                  <td><MetricName k={k} /></td>
                  {lights.map((l, i) => {
                    const v = valueOf(l, k);
                    const rel = i > 0 && v !== undefined && bv !== undefined && bv !== 0 ? (v - bv) / Math.abs(bv) : null;
                    const tone = rel === null || Math.abs(rel) < 0.005 ? "var(--text-3)" : (lowerBetter ? rel < 0 : rel > 0) ? "var(--good)" : "var(--bad)";
                    return <td key={ids[i]} className="num" data-sort={v}>{fmtNum(v)}{rel !== null && <span style={{ color: tone, fontSize: 12 }}> {fmtPct(rel)}</span>}</td>;
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
