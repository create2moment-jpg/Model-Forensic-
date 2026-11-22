import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Clock, Hourglass, Loader2, XCircle } from "lucide-react";
import { useState } from "react";
import type { TaskView } from "../api/types";
import { Pill } from "./ui";

const COST_TONE: Record<string, "outline" | "warn" | "bad"> = { lightweight: "outline", medium: "outline", heavy: "warn", very_heavy: "bad" };
const secs = (s: number): string => (s < 1 ? `${Math.round(s * 1000)} ms` : s < 90 ? `${s.toFixed(1)} s` : `${Math.round(s / 60)} min`);

function StateIcon({ t }: { t: TaskView }) {
  if (t.state === "running") return <Loader2 size={13} className="spin" color="var(--accent)" />;
  if (t.state === "waiting") return <Hourglass size={13} color="var(--text-3)" />;
  if (t.state === "queued") return <Clock size={13} color="var(--text-3)" />;
  if (t.state === "done") return <CheckCircle2 size={13} color="var(--good)" />;
  if (t.state === "failed") return <XCircle size={13} color="var(--bad)" />;
  return <AlertCircle size={13} color="var(--warn)" />;
}

/** What the run page is loading, live: every task with its state, elapsed time, cost class, cache state, why it is
 *  heavy and — when it failed — why. Never an empty wait: the shell is already there and this says what comes next. */
export function RunLoadingPanel({ tasks, mode, onMode }: { tasks: TaskView[]; mode: "light" | "full"; onMode: (m: "light" | "full") => void }) {
  const active = tasks.filter((t) => t.active);
  const failed = tasks.filter((t) => t.state === "failed");
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? (active.length > 0 || failed.length > 0);
  const total = tasks.length ? Math.max(...tasks.map((t) => (t.ended_at ?? Date.now() / 1000))) - Math.min(...tasks.map((t) => t.created_at)) : 0;
  const warm = tasks.filter((t) => t.cache === "warm").length;
  const summary = active.length
    ? `Loading ${active.length} of ${tasks.length} part${tasks.length === 1 ? "" : "s"} · ${active.filter((t) => t.state === "running").map((t) => t.title).join(", ") || "queued"}`
    : tasks.length
      ? `Loaded ${tasks.length - failed.length}/${tasks.length} part${tasks.length === 1 ? "" : "s"} in ${secs(Math.max(0, total))}${warm ? ` · ${warm} from cache` : ""}${failed.length ? ` · ${failed.length} failed` : ""}`
      : "Nothing loading";
  return (
    <div className="card run-loading" data-testid="run-loading" style={{ marginBottom: 12 }}>
      <div className="row" style={{ padding: "8px 12px", gap: 8 }}>
        <button className="btn ghost sm icon" aria-label={expanded ? "Hide loading details" : "Show loading details"} onClick={() => setOpen(!expanded)}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        {active.length ? <Loader2 size={14} className="spin" color="var(--accent)" /> : failed.length ? <XCircle size={14} color="var(--bad)" /> : <CheckCircle2 size={14} color="var(--good)" />}
        <span className="ellipsis" style={{ fontSize: 13 }}>{summary}</span>
        <span className="spacer" />
        <div className="seg" role="group" aria-label="Loading mode">
          <button className={mode === "light" ? "on" : ""} aria-pressed={mode === "light"} title="Light Mode: the run, stages, configs and file list; heavy parts load when you open their tab" onClick={() => onMode("light")}>Light</button>
          <button className={mode === "full" ? "on" : ""} aria-pressed={mode === "full"} title="Full Mode: everything in the background — metric histories, the baseline comparison, the code diff" onClick={() => onMode("full")}>Full</button>
        </div>
      </div>
      {expanded && (
        <table className="t" style={{ fontSize: 12.5 }}>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id} data-state={t.state}>
                <td style={{ width: 22 }}><StateIcon t={t} /></td>
                <td title={t.why}>{t.title}{t.progress.step ? <span className="faint"> — {String(t.progress.step)}</span> : null}</td>
                <td><Pill tone={COST_TONE[t.cost] ?? "outline"}>{t.cost.replace("_", " ")}</Pill></td>
                <td className="faint">{t.state === "waiting" ? "waits for its inputs" : t.state}{t.cache !== "unknown" && t.state === "done" ? ` · ${t.cache}` : ""}</td>
                <td className="r num faint">{t.started_at ? secs(t.elapsed_s) : "—"}</td>
                <td className="wrap faint" style={{ maxWidth: 380 }}>{t.error ? <span className="bad">{t.error}</span> : t.why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
