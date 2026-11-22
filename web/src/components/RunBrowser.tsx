import { useQuery } from "@tanstack/react-query";
import { FolderOpen, Search, Star, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api, refStr } from "../api/client";
import type { Run } from "../api/types";
import { fmtAgo, shortSha } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { useApp } from "./AppState";
import { StatusPill } from "./ui";

/** Pick a run the way the Runs page finds one: an experiment first (yours starred), then its runs — name, run id,
 *  status, owner, branch, age — with a search box and Mine / Completed filters. */
export function RunBrowser({ onPick, onClose, title = "Pick a run" }: { onPick: (id: string) => void; onClose: () => void; title?: string }) {
  const { workspace } = useApp();
  const exps = useQuery({ queryKey: ["experiments", workspace], queryFn: () => api.experiments(workspace), staleTime: 60_000 });
  const [exp, setExp] = useState<string | null>(null);
  const [expFilter, setExpFilter] = useState("");
  const [text, setText] = useState("");
  const [mine, setMine] = useState(false);
  const [done, setDone] = useState(false);
  const dq = useDebounced(text, 150);
  const query = [exp ? `experiment:"${exp}"` : "", mine ? "user:me" : "", done ? "status:completed" : "", dq].filter(Boolean).join(" ");
  const runs = useQuery({ queryKey: ["runs", "browser", query, workspace], queryFn: () => api.search(query, 300, 0, workspace) });
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const list = useMemo(() => (exps.data ?? []).filter((e) => e.name.toLowerCase().includes(expFilter.toLowerCase()))
    .sort((a, b) => Number(b.mine) - Number(a.mine) || (b.last ?? "").localeCompare(a.last ?? "")), [exps.data, expFilter]);
  const rows: Run[] = runs.data?.runs ?? [];
  return createPortal(
    <>
      <div className="chart-backdrop" onClick={onClose} />
      <div className="run-browser card" role="dialog" aria-label={title} data-testid="run-browser">
        <div className="card-head"><h3>{title}</h3><span className="spacer" /><button className="btn ghost sm icon" aria-label="Close" onClick={onClose}><X size={14} /></button></div>
        <div className="rb-body">
          <div className="rb-exps">
            <div className="input-wrap" style={{ margin: 8 }}><Search size={13} /><input className="input" placeholder="Filter experiments" value={expFilter} onChange={(e) => setExpFilter(e.target.value)} /></div>
            <button className={`rb-exp ${exp === null ? "on" : ""}`} onClick={() => setExp(null)}><span className="trunc">All experiments</span></button>
            {list.map((e) => (
              <button key={e.name} className={`rb-exp ${exp === e.name ? "on" : ""}`} onClick={() => setExp(e.name)} title={e.name}>
                {e.mine ? <Star size={11} fill="var(--warn)" color="var(--warn)" /> : <span style={{ width: 11 }} />}
                <span className="trunc" style={{ flex: 1 }}>{e.name}</span><span className="faint">{e.runs}</span>
              </button>
            ))}
          </div>
          <div className="rb-runs">
            <div className="row" style={{ gap: 8, padding: 8 }}>
              <div className="input-wrap" style={{ flex: 1 }}><Search size={13} /><input className="input" autoFocus placeholder="Search name, run id, branch, tag… (same syntax as the Runs page)" value={text} onChange={(e) => setText(e.target.value)} /></div>
              <button className={`filter-chip ${mine ? "on" : ""}`} onClick={() => setMine(!mine)}>Mine</button>
              <button className={`filter-chip ${done ? "on" : ""}`} onClick={() => setDone(!done)}>Completed</button>
            </div>
            <div className="table-wrap" style={{ maxHeight: "calc(80vh - 140px)" }}>
              <table className="t fixed">
                <thead><tr><th style={{ width: "30%" }}>Run</th><th style={{ width: "20%" }}>Run ID</th><th style={{ width: 110 }}>Status</th><th>Owner</th><th>Branch</th><th className="r" style={{ width: 90 }}>Created</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={refStr(r.ref)} className="clickable" onClick={() => { onPick(refStr(r.ref)); onClose(); }}>
                      <td className="trunc"><b>{r.display_name ?? r.name}</b></td>
                      <td className="mono faint trunc">{r.ref.run_id}</td>
                      <td><StatusPill status={r.status} /></td>
                      <td className="trunc">{r.user ?? "—"}</td>
                      <td className="mono faint trunc">{r.git.branch ? `${r.git.branch}${r.git.commit ? ` @ ${shortSha(r.git.commit)}` : ""}` : "—"}</td>
                      <td className="r faint" data-sort={r.created_at ?? ""}>{fmtAgo(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {runs.data && rows.length === 0 && <div className="muted" style={{ padding: 16 }}>No runs match.</div>}
            </div>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}

/** A small button that opens the browser. */
export function BrowseRunsButton({ onPick, title }: { onPick: (id: string) => void; title?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="btn ghost sm" title="Browse runs by experiment" aria-label="Browse runs" onClick={() => setOpen(true)}><FolderOpen size={13} /> Browse</button>
      {open && <RunBrowser onPick={onPick} onClose={() => setOpen(false)} title={title} />}
    </>
  );
}
