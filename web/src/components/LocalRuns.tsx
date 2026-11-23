import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, FolderSearch, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { LocalPreview } from "../api/types";
import { useApp } from "./AppState";
import { Card, ErrorBox, Loading, Pill } from "./ui";

/** Add a local training / evaluation folder (one run, or a folder of runs): check what would be read, then add —
 *  read in place, nothing is written into it. ``onAdded`` gets the runs found (to open one right away). */
export function LocalRunAdd({ onAdded }: { onAdded?: (runs: { ref: string; name: string; path: string | null }[], added: LocalPreview) => void }) {
  const qc = useQueryClient();
  const { toast } = useApp();
  const [path, setPath] = useState("");
  const pv = useMutation({ mutationFn: () => api.localRunsPreview(path.trim()) });
  const add = useMutation({
    mutationFn: () => api.localRunsAdd(path.trim()),
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: ["local-runs"] });
      void qc.invalidateQueries({ queryKey: ["backends"] });
      void qc.invalidateQueries({ queryKey: ["runs"] });
      const mine = d.runs.filter((r) => d.added.runs.some((x) => r.path === x.path));
      toast(`${mine.length} local run(s) added — read in place`, "good");
      setPath(""); pv.reset();
      onAdded?.(mine, d.added);
    },
  });
  return (
    <div className="col" style={{ gap: 8 }} data-testid="local-run-add">
      <form className="row" style={{ gap: 6 }} onSubmit={(e) => { e.preventDefault(); if (path.trim()) pv.mutate(); }}>
        <div className="input-wrap" style={{ flex: 1, minWidth: 320 }}><FolderOpen size={13} />
          <input className="input mono" aria-label="Local folder" placeholder="/path/to/run_output   (or a folder of runs, e.g. lightning_logs/..)" value={path} onChange={(e) => { setPath(e.target.value); pv.reset(); }} /></div>
        <button type="submit" className="btn" disabled={!path.trim() || pv.isPending}><FolderSearch size={14} /> Check</button>
        <button type="button" className="btn primary" disabled={!pv.data?.ok || add.isPending} onClick={() => add.mutate()}><Plus size={14} /> Add</button>
      </form>
      {pv.error && <ErrorBox error={pv.error} />}
      {add.error && <ErrorBox error={add.error} />}
      {pv.data && (pv.data.ok ? (
        <div className="note" data-testid="local-preview">
          <div className="col" style={{ gap: 4, minWidth: 0 }}>
            <b>{pv.data.runs.length} run(s) found</b>
            {pv.data.runs.slice(0, 8).map((r) => (
              <div key={r.path} className="faint" style={{ fontSize: 12 }}><span className="mono">{r.name}</span> — metrics: {r.metrics.join(", ") || "none"} · configs: {r.configs.length} · logs: {r.logs.length}</div>
            ))}
          </div>
        </div>
      ) : <div className="note warn">{pv.data.problem}</div>)}
    </div>
  );
}

/** Settings → Local runs: the folders read in place (config/local-runs.local.yaml), the runs found, add / remove. */
export function LocalRunsSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["local-runs"], queryFn: api.localRuns });
  const save = useMutation({ mutationFn: (folders: string[]) => api.saveLocalRuns(folders), onSuccess: () => void qc.invalidateQueries({ queryKey: ["local-runs"] }) });
  return (
    <Card title={<span className="row"><FolderOpen size={15} /> Local runs</span>} sub="Training / evaluation output folders on this machine, analysed in place — read-only: nothing is copied into them, written to them or kept in sync">
      {q.isLoading || !q.data ? <Loading /> : (
        <div className="col" style={{ gap: 12 }} data-testid="local-runs-settings">
          <LocalRunAdd />
          {q.data.error && <ErrorBox error={new Error(q.data.error)} />}
          {q.data.folders.length > 0 ? (
            <table className="t compact" data-nosort>
              <thead><tr><th>Folder</th><th>Runs</th><th /></tr></thead>
              <tbody>{q.data.folders.map((f) => {
                const runs = q.data!.runs.filter((r) => r.path?.startsWith(f));
                return (
                  <tr key={f}>
                    <td className="mono" style={{ wordBreak: "break-all" }}>{f}</td>
                    <td>{runs.length ? runs.slice(0, 6).map((r) => <Link key={r.ref} to={`/runs/${encodeURIComponent(r.ref)}`} className="row" style={{ gap: 4 }}>{r.name} <Pill tone="outline">{r.status}</Pill></Link>) : <span className="faint">none found now</span>}</td>
                    <td className="act"><button className="btn ghost sm icon" aria-label={`Remove ${f}`} title="Stop reading this folder (the folder is not touched)" onClick={() => save.mutate(q.data!.folders.filter((x) => x !== f))}><Trash2 size={13} /></button></td>
                  </tr>
                );
              })}</tbody>
            </table>
          ) : <div className="faint">No local folders yet. They appear as the workspace <span className="mono">local-runs</span> next to your Azure ML workspaces.</div>}
          <div className="faint" style={{ fontSize: 12 }}>Read: PyTorch Lightning <span className="mono">metrics.csv</span> (newest version), the kit's <span className="mono">metrics.jsonl</span>, configs (<span className="mono">*config*.yaml</span>, <span className="mono">hparams.yaml</span>), logs (<span className="mono">*.log</span>), evaluation files. Saved in <span className="mono">{q.data.path}</span>; also <span className="mono">mf local add|list|remove</span>.</div>
        </div>
      )}
    </Card>
  );
}

/** Runs page: "Analyse a local run…" — add a folder and open its run. */
export function LocalRunButton() {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  return (
    <div style={{ position: "relative" }}>
      <button className="btn" onClick={() => setOpen(!open)} aria-expanded={open}><FolderOpen size={14} /> Analyse a local run…</button>
      {open && (
        <div className="card popover-card" style={{ position: "absolute", right: 0, top: 40, zIndex: 30, width: 640, padding: 12 }}>
          <LocalRunAdd onAdded={(runs) => { setOpen(false); if (runs.length === 1) nav(`/runs/${encodeURIComponent(runs[0]!.ref)}`); }} />
        </div>
      )}
    </div>
  );
}
