import { CheckCircle2, Loader2, RotateCcw, X, XCircle } from "lucide-react";
import { api } from "../api/client";
import { fmtBytes } from "../lib/format";
import { useApp } from "./AppState";

export function JobTray() {
  const { jobs, trayOpen, setTrayOpen, trackJob } = useApp();
  if (!trayOpen) return null;
  return (
    <div className="drawer">
      <div className="card-head">
        <h3>Background jobs</h3>
        <span className="muted">{jobs.filter((j) => j.status === "running").length} running</span>
        <div className="right"><button className="btn ghost sm icon" onClick={() => setTrayOpen(false)}><X size={14} /></button></div>
      </div>
      <div style={{ maxHeight: 360, overflow: "auto" }}>
        {jobs.length === 0 && <div className="empty" style={{ padding: 24 }}>No jobs yet</div>}
        {jobs.map((j) => {
          const p = j.progress;
          const frac = p.total_bytes ? (p.done_bytes ?? 0) / p.total_bytes : p.total_files ? (p.done_files ?? 0) / p.total_files : j.status === "done" ? 1 : 0;
          return (
            <div key={j.id} style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)" }}>
              <div className="row">
                {j.status === "running" ? <Loader2 size={14} className="spin" color="var(--accent)" /> : j.status === "done" ? <CheckCircle2 size={14} color="var(--good)" /> : j.status === "interrupted" ? <RotateCcw size={14} color="var(--warn)" /> : <XCircle size={14} color="var(--bad)" />}
                <span className="ellipsis" style={{ fontWeight: 500 }}>{j.title || j.kind}</span>
                <span className="spacer" />
                {j.status === "running" && <button className="btn ghost sm" onClick={() => void api.cancelJob(j.id)}>Cancel</button>}
                {["interrupted", "failed", "cancelled"].includes(j.status) && j.rerunnable && (
                  <button className="btn ghost sm" onClick={() => void api.rerunJob(j.id).then((r) => trackJob(r.job_id))}><RotateCcw size={12} /> Run again</button>
                )}
              </div>
              {j.kind === "download" && (
                <>
                  <div className="progress" style={{ margin: "8px 0 4px" }}><div style={{ width: `${Math.round(frac * 100)}%` }} /></div>
                  <div className="row faint" style={{ fontSize: 11.5 }}>
                    <span>{p.done_files ?? 0}/{p.total_files ?? 0} files</span>
                    <span>{fmtBytes(p.done_bytes ?? 0)}{p.total_bytes ? ` / ${fmtBytes(p.total_bytes)}` : ""}</span>
                    {!!p.cached_files && <span>{p.cached_files} cached</span>}
                    {j.status === "running" && !!p.throughput_bps && <span>{fmtBytes(p.throughput_bps)}/s</span>}
                  </div>
                </>
              )}
              {j.error && <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>{j.error}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
