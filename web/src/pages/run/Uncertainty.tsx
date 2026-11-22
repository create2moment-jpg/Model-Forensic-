import { useQuery } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { api } from "../../api/client";
import { FindingCard } from "../../components/FindingCard";
import { ParseMessages } from "../../components/ParseMessages";
import { PlotView } from "../../components/PlotView";
import { Card, ErrorBox, Loading, Pill } from "../../components/ui";

const TONE: Record<string, string> = { switched_off: "bad", at_bound: "warn", dominant: "warn", drifting: "info", settled: "good" };

/** Uncertainty-based loss weighting: what the learned σ did to every head, and whether the tasks improved or only
 *  the weights moved. */
export function UncertaintyTab({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["uncertainty", runId], queryFn: () => api.uncertainty(runId) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (!d.available) return <ParseMessages messages={d.message ? [d.message] : []} />;
  const s = d.summary ?? {};
  return (
    <div className="col" style={{ gap: 14 }} data-testid="uncertainty">
      <Card title={<span className="row"><Scale size={15} /> Learned task weights</span>} sub={d.formula}>
        <div className="row wrap" style={{ gap: 6, marginBottom: 8 }}>
          <Pill tone="outline">{s.heads} heads</Pill>
          {(["switched_off", "at_bound", "dominant", "drifting"] as const).map((k) => (s[k] ?? 0) > 0 && <Pill key={k} tone={TONE[k]}>{s[k]} {k.replace("_", " ")}</Pill>)}
          {d.bounds && <Pill tone="outline">bounds {d.bounds.join(" … ")}</Pill>}
        </div>
        <div className="table-wrap">
          <table className="t">
            <thead><tr><th>Head</th><th>Status</th><th className="r">log σ start → end</th><th className="r">weight start → end</th><th className="r">share</th><th className="r">drift (last 10 %)</th><th className="r">own loss progress</th><th>Why</th></tr></thead>
            <tbody>{(d.heads ?? []).map((h) => (
              <tr key={h.key} data-status={h.status}>
                <td className="mono">{h.head}</td>
                <td><Pill tone={TONE[h.status] ?? "outline"}>{h.status.replace("_", " ")}</Pill></td>
                <td className="r num">{h.log_sigma.first.toFixed(2)} → {h.log_sigma.last.toFixed(2)}</td>
                <td className="r num">{h.weight.first.toFixed(3)} → {h.weight.last.toFixed(3)}</td>
                <td className="r num">{(h.share * 100).toFixed(1)} %</td>
                <td className="r num">{h.drift_last_10pct >= 0 ? "+" : ""}{h.drift_last_10pct.toFixed(3)}</td>
                <td className="r num">{h.unweighted_loss_progress === null ? "—" : `${(h.unweighted_loss_progress * 100).toFixed(0)} %`}</td>
                <td className="wrap faint" style={{ maxWidth: 340, fontSize: 12 }}>{h.why}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {d.config && Object.keys(d.config).length > 0 && (
          <div className="faint mono" style={{ fontSize: 12, marginTop: 8 }}>{Object.entries(d.config).map(([k, v]) => <div key={k}>{k} = {JSON.stringify(v)}</div>)}</div>
        )}
      </Card>
      <div className="grid2">{(d.plots ?? []).map((p) => <PlotView key={p.id} spec={p} />)}</div>
      {(d.findings ?? []).map((f) => <FindingCard key={f.id} f={f} />)}
    </div>
  );
}
