import { AlertOctagon, AlertTriangle, FileWarning, Info, SearchX } from "lucide-react";
import { useState } from "react";
import type { ParseMessage } from "../api/types";
import { Pill } from "./ui";

const LOOK: Record<ParseMessage["severity"], { tone: string; label: string; Icon: typeof Info }> = {
  info: { tone: "outline", label: "info", Icon: Info },
  recoverable_warning: { tone: "warn", label: "recoverable", Icon: AlertTriangle },
  unsupported_schema: { tone: "warn", label: "unsupported schema", Icon: FileWarning },
  parse_failure: { tone: "bad", label: "parse failure", Icon: FileWarning },
  missing_data: { tone: "info", label: "missing data", Icon: SearchX },
  critical: { tone: "bad", label: "critical", Icon: AlertOctagon },
};

/** Classified reader messages: severity, file, what the reader expected vs what the file has, and the fix. An
 *  unknown format is always shown here — never a crash and never a zero in its place. */
export function ParseMessages({ messages }: { messages: ParseMessage[] | undefined }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!messages?.length) return null;
  return (
    <div className="col" style={{ gap: 6, marginBottom: 10 }} data-testid="parse-messages">
      {messages.map((m, i) => {
        const l = LOOK[m.severity] ?? LOOK.info;
        const schema = m.expected || m.observed;
        return (
          <div key={i} className={`note ${m.blocking ? "warn" : ""}`} data-severity={m.severity}>
            <l.Icon size={14} style={{ marginTop: 2, flex: "none" }} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div><Pill tone={l.tone}>{l.label}</Pill> {m.file && <span className="mono">{m.file.split("/").pop()}</span>} {m.message}</div>
              {m.remediation && <div className="faint" style={{ fontSize: 12 }}>Fix: {m.remediation}</div>}
              {schema && <button className="btn ghost sm" onClick={() => setOpen(open === i ? null : i)}>{open === i ? "Hide" : "Expected vs observed"}</button>}
              {open === i && (
                <div className="schema-diff">
                  <div><div className="faint">expected</div><pre>{JSON.stringify(m.expected, null, 2)}</pre></div>
                  <div><div className="faint">observed</div><pre>{JSON.stringify(m.observed, null, 2)}</pre></div>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
