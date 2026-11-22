import { useQuery } from "@tanstack/react-query";
import { FolderKanban } from "lucide-react";
import { api } from "../api/client";
import { useApp } from "./AppState";

/** Header selector for project profiles (shown only when config/projects has profiles): scopes every run list. */
export function ProjectSelector() {
  const { project, setProject } = useApp();
  const q = useQuery({ queryKey: ["projects"], queryFn: api.projects, staleTime: 60_000 });
  const list = q.data?.projects ?? [];
  if (list.length === 0) return null;
  const cur = list.find((p) => p.id === project);
  return (
    <label className="project-select row" title={cur ? `${cur.title} — ${cur.workspaces.join(", ")}${cur.description ? ` · ${cur.description}` : ""}` : "All projects"}>
      <FolderKanban size={14} />
      <select className="input" aria-label="Project" value={project ?? ""} onChange={(e) => setProject(e.target.value || null)}>
        <option value="">All projects</option>
        {list.map((p) => <option key={p.id} value={p.id}>{p.title}{p.unavailable.length ? " (workspace missing)" : ""}</option>)}
      </select>
    </label>
  );
}
