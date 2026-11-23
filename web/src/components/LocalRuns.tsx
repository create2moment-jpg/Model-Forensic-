import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Check, ChevronRight, Folder, FolderOpen, FolderSearch, FolderTree, Home, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { LocalPreview } from "../api/types";
import { useDebounced } from "../lib/hooks";
import { useApp } from "./AppState";
import { Card, ErrorBox, Loading, Pill } from "./ui";

/** Add a local training / evaluation folder (one run, or a folder of runs): check what would be read, then add —
 *  read in place, nothing is written into it. ``onAdded`` gets the runs found (to open one right away). */
export function LocalRunAdd({ onAdded }: { onAdded?: (runs: { ref: string; name: string; path: string | null }[], added: LocalPreview) => void }) {
  const qc = useQueryClient();
  const { toast } = useApp();
  const [path, setPath] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const pick = useRef<string | null>(null);  // a folder picked in the browser: checked right away
  const pv = useMutation({ mutationFn: () => api.localRunsPreview(path.trim()) });
  useEffect(() => { if (pick.current && pick.current === path) { pick.current = null; pv.mutate(); } }, [path]);  // eslint-disable-line react-hooks/exhaustive-deps
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
        <PathInput value={path} onChange={(v) => { setPath(v); pv.reset(); }} onSubmit={() => { if (path.trim()) pv.mutate(); }} />
        <button type="button" className="btn" aria-expanded={browsing} onClick={() => setBrowsing(!browsing)} title="Pick the folder from a list of this machine's folders"><FolderTree size={14} /> Browse…</button>
        <button type="submit" className="btn" disabled={!path.trim() || pv.isPending}><FolderSearch size={14} /> Check</button>
        <button type="button" className="btn primary" disabled={!pv.data?.ok || add.isPending} onClick={() => add.mutate()}><Plus size={14} /> Add</button>
      </form>
      {browsing && <FolderBrowser start={path} onPick={(p) => { setPath(p); setBrowsing(false); pick.current = p; }} />}
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

/** The folder field with autocomplete: the folders on this machine whose path starts with what is typed
 *  (``~`` works). ↑/↓ choose, Tab or Enter takes one (and lists its sub-folders), Esc closes. */
function PathInput({ value, onChange, onSubmit }: { value: string; onChange: (v: string) => void; onSubmit: () => void }) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(-1);
  const typed = useDebounced(value, 150);
  const sug = useQuery({ queryKey: ["local-complete", typed], queryFn: () => api.localComplete(typed), enabled: open && typed.trim().length > 0, staleTime: 10_000, retry: false });
  const items = (sug.data?.folders ?? []).filter((f) => f !== value);
  const take = (f: string): void => { onChange(f); setHi(-1); setOpen(true); };
  return (
    <div className="input-wrap path-input" style={{ flex: 1, minWidth: 320, position: "relative" }}><FolderOpen size={13} />
      <input className="input mono" aria-label="Local folder" placeholder="/path/to/run_output   (or a folder of runs) — type to complete, or Browse…" value={value} autoComplete="off" spellCheck={false}
        role="combobox" aria-expanded={open && items.length > 0} aria-autocomplete="list"
        onChange={(e) => { onChange(e.target.value); setOpen(true); setHi(-1); }} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (!open || !items.length) { if (e.key === "Enter") { e.preventDefault(); onSubmit(); } return; }
          if (e.key === "ArrowDown") { e.preventDefault(); setHi((hi + 1) % items.length); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((hi - 1 + items.length) % items.length); }
          else if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); take(items[Math.max(0, hi)]!); }
          else if (e.key === "Enter") { e.preventDefault(); if (hi >= 0) take(items[hi]!); else { setOpen(false); onSubmit(); } }
          else if (e.key === "Escape") setOpen(false);
        }} />
      {open && items.length > 0 && (
        <div className="suggest" role="listbox" data-testid="path-suggestions">
          {items.slice(0, 10).map((f, i) => (
            <div key={f} role="option" aria-selected={i === hi} className={`suggest-item mono${i === hi ? " on" : ""}`} onMouseDown={(e) => { e.preventDefault(); take(f); }}>
              <Folder size={12} style={{ flex: "none" }} />
              <span className="suggest-path"><span className="faint">{f.replace(/[^/\\]+[/\\]?$/, "")}</span><b>{f.replace(/[/\\]$/, "").split(/[/\\]/).pop()}/</b></span>
            </div>
          ))}
          <div className="faint suggest-hint">↑/↓ choose · Tab or Enter take · Esc close</div>
        </div>
      )}
    </div>
  );
}

/** Pick a folder from this machine's folders (listed by the Model Forensic server): click to open a folder, Up to its
 *  parent; folders that already look like a run (metrics.csv, TensorBoard, lightning_logs/, evaluation files) are
 *  marked. "Use this folder" fills the field and checks what would be read. */
function FolderBrowser({ start, onPick }: { start: string; onPick: (path: string) => void }) {
  const [at, setAt] = useState(start.trim());
  const [hidden, setHidden] = useState(false);
  const q = useQuery({ queryKey: ["local-browse", at, hidden], queryFn: () => api.localBrowse(at, hidden), retry: false, placeholderData: (p) => p });
  const d = q.data;
  useEffect(() => { if (d && !d.ok && at) setAt(d.parent ?? ""); }, [d, at]);  // a typed path that is not a folder: its parent
  return (
    <div className="card folder-browser" data-testid="folder-browser">
      <div className="row folder-browser-head" style={{ gap: 6 }}>
        <button type="button" className="btn sm icon" title="Up to the parent folder" aria-label="Up" disabled={!d?.parent} onClick={() => d?.parent && setAt(d.parent)}><ArrowUp size={13} /></button>
        <button type="button" className="btn sm icon" title="Home folder" aria-label="Home" onClick={() => setAt("")}><Home size={13} /></button>
        <Crumbs path={d?.path ?? at} onGo={setAt} />
        {d?.run && <Pill tone="good">looks like a run · {d.run}</Pill>}
        <button type="button" className="btn sm primary" disabled={!d?.ok} onClick={() => d && onPick(d.path)}><Check size={13} /> Use this folder</button>
      </div>
      {d && d.roots.length > 1 && <div className="row faint" style={{ gap: 6, padding: "4px 10px", fontSize: 12 }}>Go to: {d.roots.map((r) => <button key={r} type="button" className="btn ghost sm mono" onClick={() => setAt(r)}>{r}</button>)}</div>}
      {q.error ? <div style={{ padding: 10 }}><ErrorBox error={q.error} /></div> : !d ? <Loading /> : (
        <div className="folder-list" role="list">
          {d.dirs.length === 0 && <div className="faint" style={{ padding: 10 }}>No sub-folders here.</div>}
          {d.dirs.map((f) => (
            <div key={f.path} role="listitem" className="folder-row row" data-testid="folder-row">
              <button type="button" className="folder-open row" onClick={() => setAt(f.path)} title={f.path}>
                <Folder size={13} /> <span className="mono trunc">{f.name}</span>
                {f.run && <Pill tone="good">{f.run}</Pill>}
                <ChevronRight size={13} className="faint" style={{ marginLeft: "auto" }} />
              </button>
              {f.run && <button type="button" className="btn sm" onClick={() => onPick(f.path)}>Use</button>}
            </div>
          ))}
          {d.truncated && <div className="faint" style={{ padding: 8 }}>Only the first 500 folders are listed — type more of the path above to narrow it.</div>}
        </div>
      )}
      <label className="row faint" style={{ gap: 6, padding: "6px 10px", fontSize: 12, borderTop: "1px solid var(--line)" }}><input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} /> show hidden folders</label>
    </div>
  );
}

/** The current folder as a clickable trail (/ › data › runs › exp-07): wraps instead of hiding the folder's name. */
function Crumbs({ path, onGo }: { path: string; onGo: (p: string) => void }) {
  const sep = path.includes("\\") && !path.includes("/") ? "\\" : "/";
  const parts = path.split(sep).filter(Boolean);
  const lead = path.startsWith(sep) ? sep : "";
  return (
    <span className="crumbs mono" style={{ flex: 1, minWidth: 0 }} title={path} data-testid="folder-browser-path" data-path={path}>
      {lead && <button type="button" className="crumb" onClick={() => onGo(lead)}>{lead}</button>}
      {parts.map((p, i) => {
        const to = lead + parts.slice(0, i + 1).join(sep);
        const last = i === parts.length - 1;
        return <span key={to} className="row" style={{ gap: 2 }}>{i > 0 && <span className="faint">{sep}</span>}{last ? <b className="crumb-here">{p}</b> : <button type="button" className="crumb" onClick={() => onGo(to)}>{p}</button>}</span>;
      })}
    </span>
  );
}
