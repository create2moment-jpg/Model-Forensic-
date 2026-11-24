import { Eye, EyeOff } from "lucide-react";
import { PALETTE } from "./Chart";
import { SubjectLabel } from "./SubjectPicker";

/** Switch whole runs on and off in the compare charts — the baseline or a candidate leaves every curve at once (and
 *  comes back the same way), instead of hiding its curves one by one. Each chip has the run's colour in the charts. */
export function RunToggles({ runs, baseline, hidden, onChange, colours = true }: {
  runs: string[]; baseline: string | null; hidden: string[]; onChange: (hidden: string[]) => void; colours?: boolean;
}) {
  if (runs.length < 2) return null;
  const shown = runs.filter((r) => !hidden.includes(r));
  return (
    <div className="run-toggles" role="group" aria-label="Runs in the charts" data-testid="run-toggles">
      <span className="faint" style={{ fontSize: 12 }}>In the charts</span>
      {runs.map((r, i) => {
        const on = !hidden.includes(r);
        const last = on && shown.length === 1;
        const name = r.split(":").pop()!.split("+").pop()!;
        return (
          <button key={r} type="button" className={`run-toggle${on ? " on" : ""}`} aria-pressed={on} disabled={last}
            title={last ? "at least one run stays in the charts" : on ? `Hide ${name} from the charts` : `Show ${name} again`}
            onClick={() => onChange(on ? [...hidden, r] : hidden.filter((x) => x !== r))} data-run={r}>
            {colours && <i aria-hidden style={{ background: r === baseline ? `repeating-linear-gradient(90deg, ${PALETTE[i % PALETTE.length]} 0 4px, transparent 4px 7px)` : PALETTE[i % PALETTE.length] }} />}
            <span className="run-toggle-name"><SubjectLabel id={r} fallback={name} /></span>
            {r === baseline && <span className="faint" style={{ fontSize: 11 }}>baseline</span>}
            {on ? <Eye size={12} /> : <EyeOff size={12} />}
          </button>
        );
      })}
      {hidden.length > 0 && <button type="button" className="btn ghost sm" onClick={() => onChange([])}>show all</button>}
    </div>
  );
}
