import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Link2, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useApp } from "./AppState";
import { Card, ErrorBox, Loading, Pill } from "./ui";

/** Compare → Metric names: metrics only one of the two runs has, suggested pairs (the same metric under another
 *  name — a local Lightning run's ``train_loss_step`` vs ``train/loss``), and the aliases that line them up for every
 *  workspace (config/metric-aliases.local.yaml). Shown only when the names differ. */
export function MetricNames({ baseline, candidate }: { baseline: string; candidate: string }) {
  const qc = useQueryClient();
  const { toast } = useApp();
  const q = useQuery({ queryKey: ["metric-names", baseline, candidate], queryFn: () => api.metricNames(baseline, candidate), staleTime: 60_000 });
  const [pick, setPick] = useState<Record<string, boolean>>({});
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  useEffect(() => { setPick(Object.fromEntries((q.data?.suggestions ?? []).map((s) => [s.raw, true]))); }, [q.data]);
  const save = useMutation({
    mutationFn: (add: Record<string, string>) => api.saveMetricAliases({ add, runs: [baseline, candidate] }),
    onSuccess: (_d, add) => {
      toast(`${Object.keys(add).length} name(s) lined up — saved for every workspace`, "good");
      setFrom(""); setTo("");
      for (const k of ["metric-names", "compare", "compare-curves", "metric-catalog", "series", "values", "overview-cached"]) void qc.invalidateQueries({ queryKey: [k] });
    },
  });
  if (q.isLoading) return <Card title="Metric names"><Loading /></Card>;
  if (q.error) return <Card title="Metric names"><ErrorBox error={q.error} /></Card>;
  const d = q.data!;
  if (!d.only_reference.length && !d.only_candidate.length) return null;
  const rawOf = (key: string): string => d.raw_of[key] ?? key;  // an alias renames the name as the run logged it
  const chosen = Object.fromEntries(d.suggestions.filter((s) => pick[s.raw]).map((s) => [s.raw, s.to]));
  return (
    <Card fold="compare.names" title={<span className="row"><Link2 size={15} /> Metric names</span>}
      sub={`${d.matched} shared · ${d.only_reference.length} only in the baseline · ${d.only_candidate.length} only in the candidate — the same metric under another name is not compared until it is lined up`}>
      <div className="col" style={{ gap: 10 }} data-testid="metric-names">
        {d.suggestions.length > 0 && (
          <>
            <table className="t compact" data-nosort>
              <thead><tr><th style={{ width: 28 }} /><th>candidate's name</th><th /><th>baseline's name</th><th>why</th></tr></thead>
              <tbody>{d.suggestions.map((s) => (
                <tr key={s.raw}>
                  <td><input type="checkbox" aria-label={`line up ${s.raw}`} checked={!!pick[s.raw]} onChange={(e) => setPick({ ...pick, [s.raw]: e.target.checked })} /></td>
                  <td className="mono">{s.raw}</td><td><ArrowRight size={12} className="faint" /></td><td className="mono">{s.to}</td>
                  <td><Pill tone={s.score >= 1 ? "good" : "outline"}>{s.why}</Pill></td>
                </tr>
              ))}</tbody>
            </table>
            <div><button className="btn primary sm" disabled={!Object.keys(chosen).length || save.isPending} onClick={() => save.mutate(chosen)}>Line up {Object.keys(chosen).length} pair(s)</button></div>
          </>
        )}
        <div className="row wrap" style={{ gap: 6 }}>
          <span className="faint" style={{ fontSize: 12 }}>Pair by hand:</span>
          <select className="select" aria-label="Candidate metric" value={from} onChange={(e) => setFrom(e.target.value)} style={{ maxWidth: 280 }}>
            <option value="">only in the candidate…</option>
            {d.only_candidate.map((k) => <option key={k} value={rawOf(k)}>{k}</option>)}
          </select>
          <ArrowRight size={12} className="faint" />
          <select className="select" aria-label="Baseline metric" value={to} onChange={(e) => setTo(e.target.value)} style={{ maxWidth: 280 }}>
            <option value="">only in the baseline…</option>
            {d.only_reference.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <button className="btn sm" disabled={!from || !to || save.isPending} onClick={() => save.mutate({ [from]: to })}><Plus size={12} /> Line up</button>
        </div>
        <div className="faint" style={{ fontSize: 11.5 }}>Saved in <span className="mono">{d.path}</span> ({Object.keys(d.aliases).length} alias(es)) — applied to every workspace, local runs included; nothing is renamed in the runs themselves.</div>
        {save.error && <ErrorBox error={save.error} />}
      </div>
    </Card>
  );
}
