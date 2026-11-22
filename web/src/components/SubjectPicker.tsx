import { Plus, X } from "lucide-react";
import { useState } from "react";
import { RunPicker } from "./RunPicker";

/** The runs of a subject: ``group:<run>+<run>`` (several runs that together hold one model's results) or one run. */
export const membersOf = (v: string | null | undefined): string[] => (!v ? [] : v.startsWith("group:") ? v.slice(6).split("+").filter(Boolean) : [v]);
export const toSubject = (runs: string[]): string => {
  const u = [...new Set(runs)];
  return u.length > 1 ? `group:${u.join("+")}` : u[0] ?? "";
};

/** A baseline or candidate: one run, or several run ids whose results belong together (training in one job,
 *  evaluation in another, a resumed run). They are treated as one subject: metrics per member, configs and files of
 *  all of them. */
export function SubjectPicker({ value, onChange, placeholder = "Select run…", width = 320, label }: {
  value: string | null; onChange: (v: string) => void; placeholder?: string; width?: number; label?: string;
}) {
  const members = membersOf(value);
  const [adding, setAdding] = useState(false);
  if (members.length === 0) return <RunPicker value={null} onChange={onChange} placeholder={placeholder} width={width} />;
  return (
    <div className="subject" style={{ width }} data-testid="subject-picker" aria-label={label}>
      <div className="row wrap" style={{ gap: 4 }}>
        {members.map((m) => (
          <span key={m} className="tag row mono" style={{ gap: 4 }} title={m}>
            <span className="ellipsis" style={{ maxWidth: width - 60 }}>{m}</span>
            <X size={11} style={{ cursor: "pointer" }} aria-label={`Remove ${m}`} onClick={() => onChange(toSubject(members.filter((x) => x !== m)))} />
          </span>
        ))}
        {!adding && (
          <button type="button" className="btn ghost sm" title="Add another run id that holds results of the same model (e.g. its evaluation job)" onClick={() => setAdding(true)}>
            <Plus size={12} /> run
          </button>
        )}
      </div>
      {members.length > 1 && <div className="faint" style={{ fontSize: 11.5, marginTop: 2 }}>{members.length} runs, compared as one</div>}
      {adding && (
        <div style={{ marginTop: 4 }}>
          <RunPicker value={null} placeholder="Add a run with results of the same model…" width={width} onChange={(v) => { onChange(toSubject([...members, v])); setAdding(false); }} />
        </div>
      )}
    </div>
  );
}
