import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronDown, Cloud, FolderOpen, Layers } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Backend } from "../api/types";
import { useApp } from "./AppState";

const subtitle = (b: Backend): string =>
  b.kind === "azureml" ? [b.resource_group, b.workspace].filter(Boolean).join(" / ") : b.root ?? b.kind;

/** Top-bar dropdown that scopes search, home, palette and pickers to one Azure ML workspace. */
export function WorkspaceSelector() {
  const { workspace, setWorkspace } = useApp();
  const backends = useQuery({ queryKey: ["backends"], queryFn: api.backends, staleTime: 60_000 });
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent): void => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);
  const list = backends.data ?? [];
  const current = list.find((b) => b.name === workspace);
  const icon = (b?: Backend) => (!b ? <Layers size={14} /> : b.kind === "azureml" ? <Cloud size={14} /> : <FolderOpen size={14} />);
  const choose = (v: string | null): void => { setWorkspace(v); setOpen(false); };

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button className="btn" style={{ maxWidth: 280 }} onClick={() => setOpen(!open)} title="Workspace — scopes search and lists">
        {icon(current)}
        <span className="ellipsis">{current ? current.title : "All workspaces"}</span>
        <ChevronDown size={13} className="faint" />
      </button>
      {open && (
        <div className="palette" style={{ position: "absolute", left: 0, top: 38, width: 340, zIndex: 40 }}>
          <div className="palette-group">Workspace</div>
          <div className="palette-list" style={{ maxHeight: 360, paddingTop: 0 }}>
            <WsItem active={!workspace} onClick={() => choose(null)} icon={<Layers size={15} />} title="All workspaces" sub={`${list.length} configured`} />
            {list.map((b) => (
              <WsItem key={b.name} active={workspace === b.name} disabled={!b.available} onClick={() => b.available && choose(b.name)}
                icon={b.available ? icon(b) : <AlertTriangle size={15} color="var(--warn)" />} title={b.title}
                sub={b.available ? subtitle(b) : `unavailable — ${b.error ?? "not connected"}`} />
            ))}
          </div>
          <div className="faint" style={{ fontSize: 11.5, padding: "8px 12px", borderTop: "1px solid var(--line)" }}>
            Workspaces are configured in <code>model-forensic.yaml</code> (<code>backends:</code>).
          </div>
        </div>
      )}
    </div>
  );
}

function WsItem({ active, disabled, onClick, icon, title, sub }: { active: boolean; disabled?: boolean; onClick: () => void; icon: React.ReactNode; title: string; sub: string }) {
  return (
    <div className={`palette-item ${active ? "active" : ""}`} style={{ opacity: disabled ? 0.55 : 1, cursor: disabled ? "not-allowed" : "pointer" }} onClick={onClick} title={sub}>
      {icon}
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="ellipsis" style={{ color: "var(--text)" }}>{title}</div>
        <div className="ellipsis faint" style={{ fontSize: 11.5 }}>{sub}</div>
      </div>
      {active && <Check size={14} color="var(--accent-2)" />}
    </div>
  );
}
