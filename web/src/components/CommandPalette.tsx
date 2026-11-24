import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bot, FolderPlus, Keyboard, Download, FileText, GitCompare, Home, Layers, Moon, Play, RefreshCw, Microscope, Search, Settings, Sun, TrendingDown, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { planAndOpen } from "../pages/Prefetch";
import { api, refStr } from "../api/client";
import { useDebounced } from "../lib/hooks";
import { useApp } from "./AppState";
import { StatusPill } from "./ui";

interface Item { id: string; group: string; label: ReactNode; hint?: string; icon: ReactNode; run: () => void }

export function CommandPalette({ toggleTheme, onShortcuts }: { toggleTheme: () => void; onShortcuts?: () => void }) {
  const { paletteOpen, setPaletteOpen, trackJob, toast, workspace } = useApp();
  const nav = useNavigate();
  const loc = useLocation();
  const [text, setText] = useState("");
  const [idx, setIdx] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const dq = useDebounced(text, 120);
  const runs = useQuery({ queryKey: ["runs", "palette", dq, workspace], queryFn: () => api.search(dq, 8, 0, workspace), enabled: paletteOpen });
  const failed = useQuery({ queryKey: ["runs", "palette-failed", workspace], queryFn: () => api.search("status:failed", 1, 0, workspace), enabled: paletteOpen });
  // pasted text that names one run (an id, <workspace>:<id>, a studio link, a local folder) → open it directly
  const pasteable = /:\/\/|^[~/]|^[A-Za-z]:\\|^[\w.-]+:[\w.-]+$/.test(dq.trim()) || (/^[\w.-]{6,}$/.test(dq.trim()) && /\d/.test(dq));
  const resolved = useQuery({ queryKey: ["resolve", dq], queryFn: () => api.resolve(dq.trim()), enabled: paletteOpen && pasteable, staleTime: 30_000, retry: false });
  const currentRun = loc.pathname.startsWith("/runs/") ? decodeURIComponent(loc.pathname.split("/")[2] ?? "") : null;

  useEffect(() => {
    if (paletteOpen) { setText(""); setIdx(0); setTimeout(() => input.current?.focus(), 10); }
  }, [paletteOpen]);

  const close = (): void => setPaletteOpen(false);
  const go = (path: string) => () => { nav(path); close(); };

  const items = useMemo<Item[]>(() => {
    const t = text.toLowerCase();
    const actions: Item[] = [
      { id: "home", group: "Navigate", label: "Today", hint: "g h", icon: <Home size={15} />, run: go("/") },
      { id: "studies", group: "Navigate", label: "Studies", hint: "g t", icon: <Microscope size={15} />, run: go("/studies") },
      { id: "new-study", group: "Actions", label: "Start a new study", icon: <Microscope size={15} />, run: go("/studies?new=1") },
      { id: "runs", group: "Navigate", label: "Search runs", hint: "g r", icon: <Search size={15} />, run: go(`/runs${text ? `?q=${encodeURIComponent(text)}` : ""}`) },
      { id: "progression", group: "Navigate", label: "Plot progression across runs", hint: "g p", icon: <TrendingDown size={15} />, run: go("/progression") },
      { id: "compare", group: "Navigate", label: "Compare runs", hint: "g c", icon: <GitCompare size={15} />, run: go("/compare") },
      { id: "ai", group: "Navigate", label: "Ask AI", hint: "g a", icon: <Bot size={15} />, run: go("/ai") },
      { id: "local", group: "Navigate", label: "Local folders (read in place)", hint: "g l", icon: <Search size={15} />, run: go("/local") },
      { id: "ledger", group: "Navigate", label: "Experiment ledger", icon: <Microscope size={15} />, run: go("/ledger") },
      { id: "knowledge", group: "Navigate", label: "Knowledge", icon: <Layers size={15} />, run: go("/knowledge") },
      { id: "health", group: "Navigate", label: "Connection health (diagnostics)", icon: <Settings size={15} />, run: go("/diagnostics") },
      { id: "lib", group: "Navigate", label: "Analysis & prompt library", icon: <Layers size={15} />, run: go("/library") },
      { id: "reports", group: "Navigate", label: "Generate report", icon: <FileText size={15} />, run: go("/reports") },
      { id: "settings", group: "Navigate", label: "Settings · LLM providers (BYOK)", icon: <Settings size={15} />, run: go("/settings") },
      { id: "sync", group: "Actions", label: "Sync runs from backends", icon: <RefreshCw size={15} />, run: () => { void api.sync(workspace).then((r) => trackJob(r.job_id)); close(); } },
      { id: "keys", group: "Actions", label: "Keyboard shortcuts", hint: "?", icon: <Keyboard size={15} />, run: () => { close(); onShortcuts?.(); } },
      { id: "theme", group: "Actions", label: "Toggle light / dark theme", icon: document.documentElement.dataset.theme === "dark" ? <Sun size={15} /> : <Moon size={15} />, run: () => { toggleTheme(); close(); } },
    ];
    const latestFailed = failed.data?.runs[0];
    if (latestFailed) actions.push({ id: "failed", group: "Actions", label: <>Open latest failed job <span className="faint">· {latestFailed.name}</span></>, icon: <XCircle size={15} />, run: go(`/runs/${encodeURIComponent(refStr(latestFailed.ref))}`) });
    if (currentRun) {
      actions.unshift(
        { id: "an", group: "Current run", label: "Analyze current run", icon: <Play size={15} />, run: go(`/runs/${encodeURIComponent(currentRun)}?tab=findings&analyze=1`) },
        { id: "dl", group: "Current run", label: "Plan a fast-debug prefetch (review, then download)", icon: <Download size={15} />, run: () => { void planAndOpen(nav, { run: currentRun, profile: "fast_debug" }).catch((e: unknown) => toast(String(e), "bad")); close(); } },
        { id: "studyc", group: "Current run", label: "Start a study of the current run", icon: <Microscope size={15} />, run: go(`/studies?runs=${encodeURIComponent(currentRun)}`) },
        { id: "askc", group: "Current run", label: "Ask AI about current run", icon: <Bot size={15} />, run: go(`/runs/${encodeURIComponent(currentRun)}?tab=ai`) },
        { id: "cmpc", group: "Current run", label: "Compare current run with baseline", icon: <GitCompare size={15} />, run: go(`/compare?candidate=${encodeURIComponent(currentRun)}`) },
      );
    }
    const filtered = actions.filter((a) => !t || String(typeof a.label === "string" ? a.label : a.id).toLowerCase().includes(t) || a.id.includes(t));
    const runItems: Item[] = (runs.data?.runs ?? []).map((r) => ({
      id: refStr(r.ref), group: runs.data?.notes[0]?.startsWith("interpreted") ? runs.data.notes[0] : "Runs",
      label: <><StatusPill status={r.status} /> <span style={{ color: "var(--text)" }}>{r.name}</span></>,
      hint: `${r.experiment ?? ""} · ${r.user ?? ""}`, icon: <ArrowRight size={15} />, run: go(`/runs/${encodeURIComponent(refStr(r.ref))}`),
    }));
    const r = resolved.data;
    const opened: Item[] = !text || !r ? [] : r.kind === "run" ? [{ id: `open:${r.ref}`, group: `Open (${r.how})`, label: <><b>{r.name}</b> <span className="faint mono">{r.ref}</span></>, icon: <ArrowRight size={15} />, run: go(`/runs/${encodeURIComponent(r.ref!)}`) }]
      : r.kind === "folder" ? [{ id: `add:${r.path}`, group: "Local folder, not added yet", label: <>Add <span className="mono">{r.path}</span> and open it</>, icon: <FolderPlus size={15} />,
        run: () => { void api.localRunsAdd(r.path!).then((d) => { close(); const mine = d.runs.filter((x) => d.added.runs.some((y) => x.path === y.path)); nav(mine.length === 1 ? `/runs/${encodeURIComponent(mine[0]!.ref)}` : "/local"); }).catch((e: unknown) => toast(String(e), "bad")); } }]
      : [];
    return text ? [...opened, ...runItems.filter((x) => !opened.some((o) => o.id === `open:${x.id}`)), ...filtered] : [...filtered, ...runItems];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, runs.data, failed.data, currentRun, resolved.data]);

  if (!paletteOpen) return null;
  const onKey = (e: React.KeyboardEvent): void => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); items[idx]?.run(); }
    else if (e.key === "Escape") close();
  };
  let lastGroup = "";
  return (
    <div className="overlay" onMouseDown={close}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <Search size={18} className="faint" />
          <input ref={input} autoFocus value={text} onChange={(e) => { setText(e.target.value); setIdx(0); }} onKeyDown={onKey}
            placeholder="Search runs, paste a run id / studio link / folder, or type a command" />
          <span className="kbd">esc</span>
        </div>
        <div className="palette-list">
          {items.map((it, i) => {
            const header = it.group !== lastGroup ? <div className="palette-group">{it.group}</div> : null;
            lastGroup = it.group;
            return (
              <div key={it.id + i}>
                {header}
                <div className={`palette-item ${i === idx ? "active" : ""}`} onMouseEnter={() => setIdx(i)} onClick={it.run}>
                  {it.icon}<span className="row ellipsis">{it.label}</span>{it.hint && <span className="hint">{it.hint}</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
