import { useQuery } from "@tanstack/react-query";
import { FolderOpen, GitCompare, Radio, TrendingDown } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { LocalRunAdd } from "../components/LocalRuns";
import { RunPicker } from "../components/RunPicker";
import { Card, Empty, ErrorBox, Loading, Pill } from "../components/ui";
import { fmtAgo } from "../lib/format";

const STATUS_TONE: Record<string, string> = { running: "good", completed: "outline", failed: "bad", queued: "accent" };

/** Local runs: training / evaluation folders on this machine, read in place — Lightning metrics.csv, TensorBoard,
 *  the kit's metrics.jsonl, evaluation result files. Open one, compare a few, or compare with an Azure ML run. */
export function LocalRunsPage() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["local-runs"], queryFn: api.localRuns, refetchInterval: 10_000 });  // status follows the folders
  const [sel, setSel] = useState<string[]>([]);
  const [azure, setAzure] = useState<string | null>(null);
  const runs = q.data?.runs ?? [];
  const toggle = (r: string): void => setSel(sel.includes(r) ? sel.filter((x) => x !== r) : [...sel, r]);
  const compare = (refs: string[]): void => nav(`/compare?baseline=${encodeURIComponent(refs[0]!)}&candidate=${encodeURIComponent(refs[1]!)}${refs.length > 2 ? `&more=${refs.slice(2).map(encodeURIComponent).join(",")}` : ""}`);
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Local runs</h1><div className="sub">Training and evaluation folders on this machine, analysed in place — read-only: nothing is copied into them or written to them. Lightning <span className="mono">metrics.csv</span>, TensorBoard, the kit's <span className="mono">metrics.jsonl</span>, evaluation result files.</div></div>
      </div>
      <Card title={<span className="row"><FolderOpen size={15} /> Add a folder</span>} sub="One run, or a folder of runs (each subfolder with results is a run)">
        <LocalRunAdd onAdded={(r) => { if (r.length === 1) nav(`/runs/${encodeURIComponent(r[0]!.ref)}`); }} />
      </Card>
      <div style={{ height: 14 }} />
      <Card flush title={`${runs.length} local run(s)`} sub="Running runs refresh on their own" right={<div className="row" style={{ gap: 6 }}>
        <button className="btn sm" disabled={sel.length < 2} onClick={() => compare(sel)}><GitCompare size={13} /> Compare {sel.length >= 2 ? sel.length : ""} selected</button>
        <button className="btn sm" disabled={!sel.length} onClick={() => nav(`/progression?runs=${sel.map(encodeURIComponent).join(",")}`)}><TrendingDown size={13} /> Progression</button>
        <span className="row" style={{ gap: 4 }}>
          <RunPicker value={azure} onChange={setAzure} placeholder="…or with a run from a workspace" width={240} />
          <button className="btn sm" disabled={!azure || sel.length !== 1} title="The workspace run as the baseline, the selected local run as the candidate" onClick={() => compare([azure!, sel[0]!])}>Compare</button>
        </span>
      </div>}>
        {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : runs.length === 0 ? (
          <Empty icon={<FolderOpen size={26} />} title="No local runs yet">Add a training or evaluation output folder above (or <span className="mono">mf local add &lt;folder&gt;</span>).</Empty>
        ) : (
          <div className="table-wrap">
            <table className="t" data-testid="local-runs-table">
              <thead><tr><th style={{ width: 28 }} /><th>Run</th><th>Folder of</th><th>Kind</th><th>Status</th><th>Found</th><th>Updated</th></tr></thead>
              <tbody>{runs.map((r) => (
                <tr key={r.ref} className={sel.includes(r.ref) ? "selected" : ""}>
                  <td><input type="checkbox" aria-label={`select ${r.name}`} checked={sel.includes(r.ref)} onChange={() => toggle(r.ref)} /></td>
                  <td><Link to={`/runs/${encodeURIComponent(r.ref)}`} style={{ fontWeight: 600 }}>{r.name}</Link><div className="faint mono trunc" style={{ fontSize: 11, maxWidth: 420 }} title={r.path ?? ""}>{r.path}</div></td>
                  <td className="faint">{r.experiment ?? "—"}</td>
                  <td>{r.kind}</td>
                  <td><Pill tone={STATUS_TONE[r.status] ?? "outline"}>{r.status === "running" && <Radio size={10} />} {r.status}</Pill></td>
                  <td className="faint" style={{ fontSize: 12 }}>{r.found || "—"}</td>
                  <td className="faint nowrap">{r.updated_at ? fmtAgo(r.updated_at) : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="faint" style={{ fontSize: 12, marginTop: 10 }}>Folders are listed in Settings → Local runs (remove one there). Metric names that differ from your workspace runs can be lined up on Compare → Metric names.</div>
    </div>
  );
}
