import { useQueries } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Card, Loading, Pill } from "../../components/ui";

/** Training setup side by side: every clue (optimizer, LR, batch, schedule, precision, …) per subject, with the
 *  cells that differ from the baseline highlighted. */
export function SetupCompare({ ids }: { ids: string[] }) {
  const qs = useQueries({ queries: ids.map((id) => ({ queryKey: ["setup-clues", id], queryFn: () => api.setupClues(id) })) });
  if (qs.some((q) => q.isLoading)) return <Loading label="Reading the setups…" />;
  const data = qs.map((q) => q.data);
  const labels = new Map<string, string>();
  data.forEach((d) => d?.clues.forEach((c) => labels.set(c.id, c.label)));
  const val = (i: number, id: string): string => { const c = data[i]?.clues.find((x) => x.id === id); return c ? JSON.stringify(c.value) : "—"; };
  const rows = [...labels.keys()];
  return (
    <Card fold="compare.setup" title="Training setup" sub="from configs, metrics and compute — cells that differ from the baseline are highlighted" flush>
      <div className="table-wrap" data-testid="setup-compare">
        <table className="t fixed" data-nosort>
          <thead><tr><th style={{ width: 180 }}>Clue</th>{ids.map((id, i) => <th key={id} className="trunc" title={id} style={{ textTransform: "none" }}>{i === 0 ? "Baseline" : `Candidate ${i}`}<div className="faint mono trunc" style={{ fontSize: 11 }}>{id.split(":").pop()}</div></th>)}</tr></thead>
          <tbody>
            {rows.map((r) => {
              const differs = ids.some((_, i) => i > 0 && val(i, r) !== val(0, r));
              return (
                <tr key={r} data-differs={differs || undefined}>
                  <td className="faint">{labels.get(r)}{differs && <Pill tone="warn">differs</Pill>}</td>
                  {ids.map((id, i) => <td key={id} className={`mono trunc ${i > 0 && val(i, r) !== val(0, r) ? "diff-cell" : ""}`} title={val(i, r)}>{val(i, r)}</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
