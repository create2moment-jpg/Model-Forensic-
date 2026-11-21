import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
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
  return (
    <div className="error-box">
      <AlertTriangle size={18} color="var(--bad)" style={{ flex: "none", marginTop: 1 }} />
      <div>
        <div style={{ fontWeight: 600 }}>{code ? code.replace(/_/g, " ") : "Something went wrong"}</div>
        <div className="muted">{e?.message ?? String(error)}</div>
      </div>
    </div>
  );
}

export const Switch = ({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) => (
  <button type="button" role="switch" aria-checked={on} aria-label={label} className={`switch ${on ? "on" : ""}`} onClick={() => onChange(!on)} />
);

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; icon?: ReactNode; count?: number }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <div key={t.id} role="tab" aria-selected={value === t.id} className={`tab ${value === t.id ? "active" : ""}`} onClick={() => onChange(t.id)}>
          {t.icon}
          {t.label}
          {t.count !== undefined && <span className="count">{t.count}</span>}
        </div>
      ))}
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

export function Card({ title, sub, right, children, flush, className }: { title?: ReactNode; sub?: ReactNode; right?: ReactNode; children: ReactNode; flush?: boolean; className?: string }) {
  return (
    <div className={`card ${className ?? ""}`}>
      {(title || right) && (
        <div className="card-head">
          {title && <h3>{title}</h3>}
          {sub && <span className="muted">{sub}</span>}
          {right && <div className="right">{right}</div>}
        </div>
      )}
      <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>
    </div>
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
