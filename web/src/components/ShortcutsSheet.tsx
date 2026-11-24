import { Keyboard, X } from "lucide-react";
import { useEffect } from "react";
import { SHORTCUTS } from "../lib/shortcuts";

/** ``?``: every keyboard shortcut on one sheet. */
export function ShortcutsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent): void => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open, onClose]);
  if (!open) return null;
  const groups = [...new Set(SHORTCUTS.map((s) => s.where))];
  return (
    <div className="overlay" onMouseDown={onClose}>
      <div className="palette shortcuts-sheet" role="dialog" aria-label="Keyboard shortcuts" data-testid="shortcuts" onMouseDown={(e) => e.stopPropagation()}>
        <div className="drawer-head"><Keyboard size={16} /><b>Keyboard shortcuts</b><span className="spacer" /><button className="btn ghost sm icon" aria-label="Close" onClick={onClose}><X size={14} /></button></div>
        <div className="shortcuts-body">
          {groups.map((g) => (
            <div key={g} className="col" style={{ gap: 6 }}>
              <div className="palette-group" style={{ padding: 0 }}>{g}</div>
              {SHORTCUTS.filter((s) => s.where === g).map((s) => (
                <div key={s.label} className="row shortcut-row">
                  <span className="row" style={{ gap: 4, minWidth: 92 }}>{s.keys.map((k, i) => <span key={i} className="kbd">{k}</span>)}</span>
                  <span>{s.label}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
