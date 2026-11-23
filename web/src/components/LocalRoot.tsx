import { Copy, FolderOpen, Settings2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useApp } from "./AppState";

/** Where a run's downloaded files are on disk: the run's folder, shown once — every file path next to it is relative
 *  to it (a pipeline stage's files are under ``stages/<stage>/``). */
export function LocalRoot({ root, compact = false }: { root: string | undefined | null; compact?: boolean }) {
  const { toast } = useApp();
  if (!root) return null;
  return (
    <div className={`local-root ${compact ? "compact" : "note"}`} data-testid="local-root" title="Every downloaded file of this run is under this folder; paths shown are relative to it. A pipeline stage's files are under stages/<stage>/.">
      <FolderOpen size={13} className="faint" style={{ flex: "none" }} />
      <span className="faint" style={{ flex: "none" }}>Run folder</span>
      <span className="mono trunc" style={{ minWidth: 0 }}>{root}</span>
      <button type="button" className="btn ghost sm icon" aria-label="Copy the run folder" title="Copy the path" onClick={() => { void navigator.clipboard?.writeText(root); toast("Copied the run folder"); }}><Copy size={12} /></button>
      {!compact && <Link to="/settings#storage" className="btn ghost sm" title="Change where downloaded files go (Settings → Storage)"><Settings2 size={12} /> change…</Link>}
    </div>
  );
}

/** A file of ``runId`` relative to the run's folder (the server's rule: a stage's files under ``stages/<stage>/``). */
export const relPath = (runId: string, a: { run?: { backend: string; run_id: string } | string | null; stage?: string | null; path: string }): string => {
  const r = typeof a.run === "string" ? a.run : a.run ? `${a.run.backend}:${a.run.run_id}` : runId;
  return r === runId || !a.stage ? a.path : `stages/${a.stage.replace(/[^A-Za-z0-9._-]+/g, "_")}/${a.path}`;
};
