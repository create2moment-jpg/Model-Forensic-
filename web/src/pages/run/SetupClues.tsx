import { useQuery } from "@tanstack/react-query";
import { Wrench } from "lucide-react";
import { api } from "../../api/client";
import { Card, ErrorBox, Loading, Pill } from "../../components/ui";

/** How the run was set up — each clue with the file / key / metric it came from; what was not found is listed. */
export function SetupCluesTab({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["setup-clues", runId], queryFn: () => api.setupClues(runId) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  return (
    <div className="col" style={{ gap: 14 }} data-testid="setup-clues">
      {d.note && <div className="note">{d.note}</div>}
      <Card title={<span className="row"><Wrench size={15} /> Training setup</span>} sub={`${d.clues.length} clues from ${d.config_keys} config keys, the metrics and the compute`} flush>
        <table className="t">
          <tbody>{d.clues.map((c) => (
            <tr key={c.id}><td style={{ width: 200 }}><b>{c.label}</b></td><td className="mono">{JSON.stringify(c.value)}</td>
              <td className="faint mono wrap" style={{ fontSize: 11.5 }} title={c.also.join("\n")}>{c.source}{c.also.length ? ` (+${c.also.length})` : ""}</td></tr>
          ))}</tbody>
        </table>
      </Card>
      {d.observations.length > 0 && (
        <Card title="Observations">
          <div className="col" style={{ gap: 6 }}>{d.observations.map((o) => (
            <div key={o.id} className={`note ${o.severity === "warning" ? "warn" : ""}`}><Pill tone={o.severity === "warning" ? "warn" : "outline"}>{o.severity}</Pill> {o.text}
              {o.evidence.length > 0 && <span className="faint mono" style={{ fontSize: 11 }}> · {o.evidence.join(", ")}</span>}</div>
          ))}</div>
        </Card>
      )}
      {d.not_found.length > 0 && <div className="faint" style={{ fontSize: 12.5 }}>Not found in the configuration: {d.not_found.join(" · ")}</div>}
    </div>
  );
}
