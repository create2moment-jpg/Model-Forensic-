import { useQuery } from "@tanstack/react-query";
import { Activity, Download, Loader2, Rows3, Workflow } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useApp } from "./AppState";

const secs = (s: number): string => (s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`);

/** Everything running in the application — run loading, prefetch tasks, jobs — visible from every page. The state
 *  comes from the server, so switching pages or reloading never hides or resets it. */
export function TaskIndicator() {
  const nav = useNavigate();
  const { setTrayOpen } = useApp();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["tasks-active"], queryFn: api.tasksActive, refetchInterval: (s) => ((s.state.data?.count ?? 0) > 0 ? 1000 : 4000) });
  const items = q.data?.active ?? [];
  const n = items.length;
  return (
    <div style={{ position: "relative" }}>
      <button className={`btn sm ${n ? "" : "ghost"}`} data-testid="task-indicator" aria-label={n ? `${n} background task${n === 1 ? "" : "s"} running` : "No background tasks running"}
        title={n ? `${n} running — click for details` : "No background tasks running"} onClick={() => setOpen(!open)}>
        {n ? <Loader2 size={14} className="spin" color="var(--accent)" /> : <Activity size={14} />}{n > 0 && <b>{n}</b>}
      </button>
      {open && (
        <div className="palette" style={{ position: "absolute", right: 0, top: 34, width: 380, zIndex: 40 }} onMouseLeave={() => setOpen(false)}>
          <div className="card-head"><h3>Running now</h3></div>
          <div className="palette-list" style={{ maxHeight: 360 }}>
            {n === 0 && <div className="empty" style={{ padding: 18 }}>Nothing is running.</div>}
            {items.map((t) => {
              const Icon = t.source === "prefetch" ? Download : t.source === "run" ? Rows3 : Workflow;
              const step = (t.progress as { step?: string }).step;
              return (
                <button key={`${t.source}:${t.id}`} className="palette-item" style={{ width: "100%", textAlign: "left" }}
                  onClick={() => { setOpen(false); if (t.link) nav(t.link); else setTrayOpen(true); }}>
                  <Icon size={14} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="ellipsis">{t.title}</div>
                    <div className="faint ellipsis" style={{ fontSize: 11.5 }}>{t.source} · {t.state}{step ? ` · ${step}` : ""}{t.subject ? ` · ${t.subject}` : ""}</div>
                  </div>
                  <span className="faint num" style={{ fontSize: 11.5 }}>{secs(t.elapsed_s)}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
