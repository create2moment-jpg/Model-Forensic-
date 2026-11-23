import { AlertTriangle, ChevronDown, ChevronRight, Inbox, Loader2, MoreHorizontal } from "lucide-react";
import { useLocalStorage } from "../lib/hooks";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import type { Label, RunStatus, Severity } from "../api/types";

export function Pill({ tone, children, dot, title, dashed }: { tone?: string; children: ReactNode; dot?: boolean; title?: string; dashed?: boolean }) {
  return (
    <span className={`pill ${tone ?? ""} ${dashed ? "dashed" : ""}`} title={title}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

const STATUS_TONE: Record<RunStatus, string> = {
  completed: "good", failed: "bad", running: "info running", queued: "outline", canceled: "warn", unknown: "outline",
};
export const StatusPill = ({ status }: { status: RunStatus }) => <Pill tone={STATUS_TONE[status]} dot>{status}</Pill>;

const SEV_TONE: Record<Severity, string> = { critical: "bad", high: "bad", medium: "warn", low: "info", info: "outline" };
export const SeverityPill = ({ s }: { s: Severity }) => <Pill tone={SEV_TONE[s]}>{s}</Pill>;

const LABEL_TONE: Record<Label, string> = {
  observed: "outline", computed: "outline", inferred: "info", hypothesized: "warn", recommended: "good", ai_generated: "ai",
};
const LABEL_HELP: Record<Label, string> = {
  observed: "Retrieved verbatim from the backend",
  computed: "Deterministically computed by an analysis",
  inferred: "Rule-based reading of computed evidence",
  hypothesized: "Candidate cause — not established",
  recommended: "Proposed action",
  ai_generated: "Produced by an LLM",
};
export const LabelPill = ({ l }: { l: Label }) => (
  <Pill tone={LABEL_TONE[l]} title={LABEL_HELP[l]} dashed={l === "hypothesized"}>{l.replace("_", " ")}</Pill>
);

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      {icon ?? <Inbox size={28} />}
      <h4>{title}</h4>
      {children && <div style={{ maxWidth: 440 }}>{children}</div>}
      {action}
    </div>
  );
}

export const Skeleton = ({ h = 16, w = "100%" }: { h?: number; w?: number | string }) => (
  <div className="skeleton" style={{ height: h, width: w }} />
);
export const Loading = ({ label = "Loading…" }: { label?: string }) => (
  <div className="row muted" style={{ padding: 24, justifyContent: "center" }}>
    <Loader2 size={16} className="spin" /> {label}
  </div>
);

export function ErrorBox({ error }: { error: unknown }) {
  const e = error as Error;
  const code = error instanceof ApiError ? error.code : undefined;
  const details = error instanceof ApiError ? (error.details as { diagnosis?: { fixes?: string[] } } | undefined) : undefined;
  const fixes = details?.diagnosis?.fixes ?? [];
  return (
    <div className="error-box">
      <AlertTriangle size={18} color="var(--bad)" style={{ flex: "none", marginTop: 1 }} />
      <div>
        <div style={{ fontWeight: 600 }}>{code ? code.replace(/_/g, " ") : "Something went wrong"}</div>
        <div className="muted">{e?.message ?? String(error)}</div>
        {fixes.length > 0 && <ol className="fixes">{fixes.map((f) => <li key={f} className="mono">{f}</li>)}</ol>}
      </div>
    </div>
  );
}

export const Switch = ({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) => (
  <button type="button" role="switch" aria-checked={on} aria-label={label} className={`switch ${on ? "on" : ""}`} onClick={() => onChange(!on)} />
);

export function Tabs<T extends string>({ tabs, value, onChange, right }: { tabs: { id: T; label: string; icon?: ReactNode; count?: number }[]; value: T; onChange: (v: T) => void; right?: ReactNode }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <div key={t.id} role="tab" aria-selected={value === t.id} className={`tab ${value === t.id ? "active" : ""}`} onClick={() => onChange(t.id)}>
          {t.icon}
          {t.label}
          {t.count !== undefined && <span className="count">{t.count}</span>}
        </div>
      ))}
      {right && <><span className="spacer" /><div className="tabs-right">{right}</div></>}
    </div>
  );
}

export function Seg<T extends string>({ options, value, onChange }: { options: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.id} className={value === o.id ? "on" : ""} onClick={() => onChange(o.id)}>{o.label}</button>
      ))}
    </div>
  );
}

/** A card. With `fold` (a stable id) its head folds / unfolds the body; the choice is remembered per id. */
export function Card({ title, sub, right, children, flush, className, fold, defaultFolded = false }: { title?: ReactNode; sub?: ReactNode; right?: ReactNode; children: ReactNode; flush?: boolean; className?: string; fold?: string; defaultFolded?: boolean }) {
  const [folded, setFolded] = useLocalStorage<boolean>(`mf.fold.${fold ?? "-"}`, defaultFolded);
  const shut = Boolean(fold) && folded;
  useEffect(() => {
    if (!fold) return;
    const on = (e: Event): void => { const d = (e as CustomEvent<{ ids: string[]; folded: boolean }>).detail; if (d.ids.includes(fold)) setFolded(d.folded); };
    window.addEventListener("mf-fold", on);
    return () => window.removeEventListener("mf-fold", on);
  }, [fold, setFolded]);
  return (
    <div className={`card ${className ?? ""} ${shut ? "folded" : ""}`} data-fold={fold}>
      {(title || right) && (
        <div className="card-head">
          {fold && (
            <button type="button" className="fold-btn" aria-expanded={!shut} aria-label={`${shut ? "Unfold" : "Fold"} ${typeof title === "string" ? title : "section"}`}
              title={shut ? "Unfold this section" : "Fold this section"} onClick={() => setFolded(!shut)}>
              {shut ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
            </button>
          )}
          {title && <h3 className={fold ? "foldable" : undefined} onClick={fold ? () => setFolded(!shut) : undefined}>{title}</h3>}
          {sub && <span className="muted">{sub}</span>}
          {right && !shut && <div className="right">{right}</div>}
        </div>
      )}
      {!shut && <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>}
    </div>
  );
}

/** Fold / unfold every foldable section on the page at once (they listen for the event). */
export function FoldAll({ ids, what = "all" }: { ids: string[]; what?: string }) {
  const set = (v: boolean): void => {
    ids.forEach((id) => { try { localStorage.setItem(`mf.fold.${id}`, JSON.stringify(v)); } catch { /* storage unavailable */ } });
    window.dispatchEvent(new CustomEvent("mf-fold", { detail: { ids, folded: v } }));
  };
  return (
    <span className="row" style={{ gap: 4 }}>
      <button type="button" className="btn ghost sm" onClick={() => set(true)}>Fold {what}</button>
      <button type="button" className="btn ghost sm" onClick={() => set(false)}>Unfold {what}</button>
    </span>
  );
}

/** Diverging bar for relative change; red when worse regardless of metric direction. */
export function DeltaBar({ rel, worse, scale = 0.2 }: { rel: number | null; worse: boolean | null; scale?: number }) {
  if (rel === null || !Number.isFinite(rel)) return <div className="bar-cell"><div className="axis" /></div>;
  const w = Math.min(50, (Math.abs(rel) / scale) * 50);
  const color = worse === null ? "var(--text-3)" : worse ? "var(--bad)" : "var(--good)";
  return (
    <div className="bar-cell">
      <div className="axis" />
      <div className="bar" style={{ background: color, width: `${w}%`, left: rel >= 0 ? "50%" : `${50 - w}%` }} />
    </div>
  );
}

export interface MenuItem { label: string; icon?: ReactNode; onSelect: () => void; hint?: string; disabled?: boolean; divider?: boolean }

/** The ``⋯`` menu: the actions that are not the page's one or two primary ones. Opens on click, closes on a pick,
 *  Escape or a click outside; ↑/↓ move between items. */
export function Menu({ items, label = "More actions" }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent): void => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent): void => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", off);
    document.addEventListener("keydown", esc);
    box.current?.querySelector<HTMLButtonElement>("[role=menuitem]:not([disabled])")?.focus();
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [open]);
  const move = (e: React.KeyboardEvent, d: number): void => {
    const all = [...(box.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not([disabled])") ?? [])];
    const i = all.indexOf(document.activeElement as HTMLButtonElement);
    all[(i + d + all.length) % all.length]?.focus();
    e.preventDefault();
  };
  return (
    <div className="menu-wrap" ref={box}>
      <button className="btn icon" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="more-actions">
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div className="menu" role="menu" onKeyDown={(e) => { if (e.key === "ArrowDown") move(e, 1); else if (e.key === "ArrowUp") move(e, -1); }}>
          {items.map((it, i) => it.divider ? <div key={`d${i}`} className="menu-divider" role="separator" /> : (
            <button key={it.label} role="menuitem" className="menu-item" disabled={it.disabled} title={it.hint} onClick={() => { setOpen(false); it.onSelect(); }}>
              <span className="menu-icon">{it.icon}</span><span>{it.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A thin progress line under the page head while parts load — the machinery is not shown, only that it moves. */
export function ProgressLine({ active, done, total }: { active: boolean; done: number; total: number }) {
  const pct = total > 0 ? Math.max(8, Math.min(100, (done / total) * 100)) : 30;
  return <div className={`progress-line${active ? " on" : ""}`} data-testid="progress-line" data-active={active ? "yes" : "no"} aria-hidden={!active}><span style={{ width: active ? `${pct}%` : "100%" }} /></div>;
}
