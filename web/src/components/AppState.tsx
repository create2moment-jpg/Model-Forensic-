import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setActiveProject } from "../api/client";
import type { Job } from "../api/types";
import { useLocalStorage } from "../lib/hooks";

interface Toast { id: number; text: string; tone?: "good" | "bad"; undo?: () => void }
interface AppState {
  jobs: Job[];
  trackJob: (id: string) => void;
  toast: (text: string, tone?: "good" | "bad") => void;
  /** Undo instead of "are you sure?": ``run`` happens after a few seconds unless Undo is pressed; meanwhile
   *  ``pending(key)`` is true so a list can hide the item at once. Leaving the page runs what is pending. */
  later: (key: string, label: string, run: () => unknown) => void;
  pending: (key: string) => boolean;
  paletteOpen: boolean;
  setPaletteOpen: (v: boolean) => void;
  trayOpen: boolean;
  setTrayOpen: (v: boolean) => void;
  /** Selected Azure ML workspace (backend name); null = all workspaces. Scopes every search. */
  workspace: string | null;
  setWorkspace: (v: string | null) => void;
  /** Selected project profile (config/projects); null = all projects. Scopes every search and experiment list. */
  project: string | null;
  setProject: (v: string | null) => void;
}
const Ctx = createContext<AppState | null>(null);
/** How long an undoable action waits before it happens. */
export const UNDO_MS = 6000;

export function AppStateProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [trayOpen, setTrayOpen] = useState(false);
  const [workspace, setWorkspaceState] = useLocalStorage<string | null>("mf.workspace", null);
  const backends = useQuery({ queryKey: ["backends"], queryFn: api.backends, staleTime: 60_000 });
  const setWorkspace = useCallback((v: string | null) => { setWorkspaceState(v); void qc.invalidateQueries({ queryKey: ["runs"] }); }, [qc, setWorkspaceState]);
  const [project, setProjectState] = useLocalStorage<string | null>("mf.project", null);
  setActiveProject(project);  // read by api.search / api.experiments on every request
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects, staleTime: 60_000 });
  const setProject = useCallback((v: string | null) => {
    setActiveProject(v);
    setProjectState(v);
    void qc.invalidateQueries({ queryKey: ["runs"] });
    void qc.invalidateQueries({ queryKey: ["experiments"] });
  }, [qc, setProjectState]);
  useEffect(() => {
    if (project && projects.data && !projects.data.projects.some((p) => p.id === project)) setProject(null);
  }, [project, projects.data, setProject]);
  // Drop a remembered workspace that is no longer configured.
  useEffect(() => {
    if (workspace && backends.data && !backends.data.some((b) => b.name === workspace)) setWorkspaceState(null);
  }, [workspace, backends.data, setWorkspaceState]);
  const [watching, setWatching] = useState<Set<string>>(new Set());
  const jobsQ = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs,
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => j.status === "running") || watching.size ? 800 : 10_000),
  });
  const jobs = jobsQ.data ?? [];

  const toast = useCallback((text: string, tone?: "good" | "bad") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3800);
  }, []);

  const [pendingKeys, setPendingKeys] = useState<Map<string, () => unknown>>(new Map());
  const later = useCallback((key: string, label: string, run: () => unknown) => {
    const id = Date.now() + Math.random();
    let done = false;
    const finish = (): void => { setPendingKeys((m) => { const n = new Map(m); n.delete(key); return n; }); setToasts((t) => t.filter((x) => x.id !== id)); };
    const go = (): void => { if (done) return; done = true; finish(); void Promise.resolve(run()); };
    setPendingKeys((m) => new Map(m).set(key, go));
    setToasts((t) => [...t, { id, text: label, undo: () => { done = true; finish(); } }]);
    setTimeout(go, UNDO_MS);
  }, []);
  const pending = useCallback((key: string) => pendingKeys.has(key), [pendingKeys]);
  useEffect(() => {  // leaving the page: what was not undone happens
    const flush = (): void => pendingKeys.forEach((go) => go());
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, [pendingKeys]);

  useEffect(() => {
    for (const id of watching) {
      const j = jobs.find((x) => x.id === id);
      if (j && j.status !== "running") {
        setWatching((w) => { const n = new Set(w); n.delete(id); return n; });
        toast(j.status === "done" ? `${j.title || j.kind}: done` : `${j.title || j.kind}: ${j.status}`, j.status === "done" ? "good" : "bad");
        if (j.kind === "sync") void qc.invalidateQueries({ queryKey: ["runs"] });
        if (j.kind === "download") void qc.invalidateQueries({ queryKey: ["overview"] });
        if (j.kind === "verify-beliefs") { void qc.invalidateQueries({ queryKey: ["beliefs"] }); void qc.invalidateQueries({ queryKey: ["belief"] }); }
      }
    }
  }, [jobs, watching, toast, qc]);

  const trackJob = useCallback((id: string) => {
    setWatching((w) => new Set(w).add(id));
    setTrayOpen(true);
    void qc.invalidateQueries({ queryKey: ["jobs"] });
  }, [qc]);

  return (
    <Ctx.Provider value={{ jobs, trackJob, toast, later, pending, paletteOpen, setPaletteOpen, trayOpen, setTrayOpen, workspace, setWorkspace, project, setProject }}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className="toast">
            <span className="pill dot" style={{ width: 8, height: 8, padding: 0, background: t.tone === "bad" ? "var(--bad)" : t.tone === "good" ? "var(--good)" : "var(--accent)" }} />
            {t.text}
            {t.undo && <button type="button" className="btn sm" style={{ marginLeft: 8 }} onClick={t.undo} data-testid="undo">Undo</button>}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useApp(): AppState {
  const c = useContext(Ctx);
  if (!c) throw new Error("AppStateProvider missing");
  return c;
}
