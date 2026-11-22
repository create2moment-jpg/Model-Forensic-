import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Job } from "../api/types";
import { useLocalStorage } from "../lib/hooks";

interface Toast { id: number; text: string; tone?: "good" | "bad" }
interface AppState {
  jobs: Job[];
  trackJob: (id: string) => void;
  toast: (text: string, tone?: "good" | "bad") => void;
  paletteOpen: boolean;
  setPaletteOpen: (v: boolean) => void;
  trayOpen: boolean;
  setTrayOpen: (v: boolean) => void;
  /** Selected Azure ML workspace (backend name); null = all workspaces. Scopes every search. */
  workspace: string | null;
  setWorkspace: (v: string | null) => void;
}
const Ctx = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [trayOpen, setTrayOpen] = useState(false);
  const [workspace, setWorkspaceState] = useLocalStorage<string | null>("mf.workspace", null);
  const backends = useQuery({ queryKey: ["backends"], queryFn: api.backends, staleTime: 60_000 });
  const setWorkspace = useCallback((v: string | null) => { setWorkspaceState(v); void qc.invalidateQueries({ queryKey: ["runs"] }); }, [qc, setWorkspaceState]);
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
    <Ctx.Provider value={{ jobs, trackJob, toast, paletteOpen, setPaletteOpen, trayOpen, setTrayOpen, workspace, setWorkspace }}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className="toast">
            <span className="pill dot" style={{ width: 8, height: 8, padding: 0, background: t.tone === "bad" ? "var(--bad)" : t.tone === "good" ? "var(--good)" : "var(--accent)" }} />
            {t.text}
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
