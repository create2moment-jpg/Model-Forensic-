import { ChevronDown, GitCompare, Layers, MessageSquareText, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { PromptTemplate } from "../api/types";
import { isMultiRun } from "../lib/prompts";

/** Searchable, category-grouped template picker. Templates that need a baseline are disabled without one. */
export function PromptPicker({ prompts, value, onChange, hasBaseline, hasOthers }: {
  prompts: PromptTemplate[]; value: string | null; onChange: (id: string | null) => void; hasBaseline: boolean; hasOthers: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent): void => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);
  const current = prompts.find((p) => p.id === value);
  const groups = useMemo(() => {
    const t = q.toLowerCase();
    const match = prompts.filter((p) => !t || [p.title, p.description, p.id, p.category, ...p.tags].join(" ").toLowerCase().includes(t));
    const g = new Map<string, PromptTemplate[]>();
    match.forEach((p) => g.set(p.category, [...(g.get(p.category) ?? []), p]));
    return [...g.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [prompts, q]);

  return (
    <div ref={ref} style={{ position: "relative", flex: 1, minWidth: 260 }}>
      <button className="btn" style={{ width: "100%", justifyContent: "flex-start" }} onClick={() => setOpen(!open)}>
        <MessageSquareText size={14} />
        <span className="ellipsis" style={{ flex: 1, textAlign: "left" }}>{current ? current.title : `Choose a template (${prompts.length}) — or write your own question below`}</span>
        {current && <span role="button" className="faint" onClick={(e) => { e.stopPropagation(); onChange(null); }}><X size={13} /></span>}
        <ChevronDown size={13} className="faint" />
      </button>
      {open && (
        <div className="palette" style={{ position: "absolute", top: 38, left: 0, width: "min(720px, 90vw)", zIndex: 40 }}>
          <div className="palette-input" style={{ padding: "10px 12px" }}>
            <Search size={15} className="faint" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search templates: backbone, radar, NaN, sweep, tracking…" />
          </div>
          <div className="palette-list" style={{ maxHeight: 460 }}>
            {groups.map(([cat, ps]) => (
              <div key={cat}>
                <div className="palette-group">{cat} · {ps.length}</div>
                {ps.map((p) => {
                  const blocked = p.needs_baseline && !hasBaseline;
                  const multi = isMultiRun(p);
                  return (
                    <div key={p.id} className={`palette-item ${p.id === value ? "active" : ""}`} style={{ alignItems: "flex-start", opacity: blocked ? 0.5 : 1 }}
                      title={blocked ? "Pick a baseline run first" : p.template}
                      onClick={() => { if (!blocked) { onChange(p.id); setOpen(false); } }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ color: "var(--text)" }}>{p.title}</div>
                        <div className="faint" style={{ fontSize: 12 }}>{p.description}</div>
                      </div>
                      <div className="row" style={{ gap: 4, flex: "none" }}>
                        {p.needs_baseline && <span className="tag" title="Compares against a baseline"><GitCompare size={10} /> baseline</span>}
                        {multi && <span className="tag" title="Works on several runs" style={hasOthers ? { color: "var(--good)" } : undefined}><Layers size={10} /> multi-run</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}
            {groups.length === 0 && <div className="palette-item">No template matches “{q}”.</div>}
          </div>
        </div>
      )}
    </div>
  );
}
