import { ChartLine } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface RowAction { label: string; icon?: React.ReactNode; onClick: () => void; hint?: string }

/** A small chart button at the end of a table row with several actions (add as chart, plot alone, plot the family
 *  together, copy …). Closes on a click elsewhere or Esc. */
export function RowActions({ actions, label = "Plot" }: { actions: RowAction[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent): void => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !menu.current?.contains(t)) setOpen(false);
    };
    const move = (): void => {  // follow the button on scroll / resize; close once it leaves the screen
      const b = ref.current?.getBoundingClientRect();
      if (!b || b.bottom < 0 || b.top > window.innerHeight) { setOpen(false); return; }
      setPos({ top: b.bottom + 4, right: window.innerWidth - b.right });
    };
    const esc = (e: KeyboardEvent): void => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    window.addEventListener("resize", move);
    window.addEventListener("scroll", move, true);
    return () => {
      document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc);
      window.removeEventListener("resize", move); window.removeEventListener("scroll", move, true);
    };
  }, [open]);
  return (
    <div className="row-actions" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button type="button" className="icon-btn" aria-label={label} title={label} aria-expanded={open}
        onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setPos({ top: r.bottom + 4, right: window.innerWidth - r.right }); setOpen(!open); }}><ChartLine size={13} /></button>
      {open && pos && createPortal(
        <div className="row-menu" role="menu" ref={menu} style={{ position: "fixed", top: pos.top, right: pos.right }}>
          {actions.map((a) => (
            <button key={a.label} type="button" role="menuitem" title={a.hint} onClick={() => { setOpen(false); a.onClick(); }}>{a.icon}{a.label}</button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}
