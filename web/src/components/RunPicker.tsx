import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { api, refStr } from "../api/client";
import { useDebounced } from "../lib/hooks";
import { useApp } from "./AppState";
import { StatusPill } from "./ui";
import { BrowseRunsButton } from "./RunBrowser";

/** Autocomplete run selector backed by the search index. Value is a "backend:run_id" string. */
export function RunPicker({ value, onChange, placeholder = "Select run…", width = 280 }: { value: string | null; onChange: (v: string) => void; placeholder?: string; width?: number }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const dq = useDebounced(text, 150);
  const { workspace } = useApp();
  const res = useQuery({ queryKey: ["runs", "picker", dq, workspace], queryFn: () => api.search(dq, 12, 0, workspace), enabled: open });
  return (
    <div style={{ position: "relative", width }}>
      <div className="row" style={{ gap: 4 }}>
      <div className="input-wrap" style={{ flex: 1, minWidth: 0 }}>
        <Search size={14} />
        <input
          className="input" placeholder={placeholder} value={open ? text : value ?? ""}
          onFocus={() => { setOpen(true); setText(""); }}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onChange={(e) => setText(e.target.value)}
        />
      </div>
      <BrowseRunsButton onPick={onChange} title={placeholder.replace(/…$/, "")} />
      </div>
      {open && (
        <div className="palette" style={{ position: "absolute", top: 36, left: 0, width: Math.max(width, 380), zIndex: 50 }}>
          <div className="palette-list" style={{ maxHeight: 300 }}>
            {(res.data?.runs ?? []).map((r) => (
              <div key={refStr(r.ref)} className="palette-item" onMouseDown={() => { onChange(refStr(r.ref)); setOpen(false); }}>
                <StatusPill status={r.status} />
                <span className="ellipsis" style={{ color: "var(--text)" }}>{r.name}</span>
                <span className="hint ellipsis">{r.experiment}</span>
              </div>
            ))}
            {res.data && res.data.runs.length === 0 && <div className="palette-item">No runs</div>}
          </div>
        </div>
      )}
    </div>
  );
}
