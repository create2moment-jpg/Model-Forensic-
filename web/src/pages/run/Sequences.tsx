import { useQuery } from "@tanstack/react-query";
import { ParseMessages } from "../../components/ParseMessages";
import { PendingDownload } from "../../components/PendingDownload";
import { AlertTriangle, Loader2, TableProperties } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import type { SeqClassRow } from "../../api/types";
import { Card, ErrorBox, Pill } from "../../components/ui";
import { fmtNum } from "../../lib/format";

const short = (k: string): string => k.replace(/^AP_per_distance_(\d+)_(\d+)$/, "AP@$1.$2");
const pct = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);

/** Per-sequence evaluation parquets: per-class summary and the worst sequence×class rows. */
export function SequencesTab({ runId, stage = "" }: { runId: string; stage?: string }) {
  const [table, setTable] = useState<number | null>(null);
  const [metric, setMetric] = useState<string | null>(null);
  const [cls, setCls] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["sequences", runId, table, metric, cls], queryFn: () => api.sequences(runId, { table, metric, cls, limit: 30 }) });
  if (q.isLoading) return <div className="faint row"><Loader2 size={14} className="spin" /> downloading and reading the evaluation tables…</div>;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  if (!d.tables.length) return <><PendingDownload run={runId} pending={d.pending} /><ParseMessages messages={d.messages} /><div className="muted">No per-sequence evaluation parquets in this run{" "}(<span className="mono">evaluation/**/*.parquet</span>).</div></>;
  // the page's stage: open its table first (the buttons still switch to any other)
  const want = stage && table === null ? d.tables.findIndex((t) => t.stage === stage && t.recognized) : -1;
  if (want >= 0 && d.selected !== want) { queueMicrotask(() => setTable(want)); }
  const sel = d.selected !== undefined ? d.tables[d.selected] : undefined;
  const m = metric ?? sel?.metric ?? "AP";
  const worstCols = d.worst?.length ? Object.keys(d.worst[0]!).filter((k) => k !== "sequence" && k !== "class") : [];
  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="row wrap" style={{ gap: 6 }}>
        <TableProperties size={15} className="faint" />
        {d.tables.map((t, i) => (
          <button key={i} className={`btn sm ${d.selected === i ? "primary" : ""}`} disabled={!t.recognized} title={t.note} onClick={() => { setTable(i); setMetric(null); }}>
            {t.stage ? <span className="mono">{t.stage}</span> : null} {t.path.split("/").pop()} {!t.recognized && <AlertTriangle size={12} />}
          </button>
        ))}
      </div>
      <PendingDownload run={runId} pending={d.pending} />
      {d.messages ? <ParseMessages messages={d.messages} /> : d.tables.filter((t) => !t.recognized).map((t) => <div key={t.path} className="note"><AlertTriangle size={14} /> <span className="mono">{t.path.split("/").pop()}</span>: {t.note}</div>)}
      {sel?.per_class && (
        <Card title="Per class" sub={`${sel.sequences} sequences · ${sel.kind} table · mean over sequences, counts summed`} flush>
          <div className="table-wrap">
            <table className="t">
              <thead><tr><th>Class</th><th className="r">Seq.</th>{(sel.metrics ?? []).slice(0, 5).map((k) => <th key={k} className="r" title={k}>{short(k)}</th>)}{sel.kind === "detection" && <><th className="r">Precision</th><th className="r">Recall</th><th className="r">FP / GT</th><th className="r">GT</th><th className="r">FP</th><th className="r">FN</th></>}</tr></thead>
              <tbody>
                {sel.per_class.map((r: SeqClassRow) => (
                  <tr key={r.class} className={cls === r.class ? "selected" : ""} style={{ cursor: "pointer" }} onClick={() => setCls(cls === r.class ? null : r.class)}>
                    <td><b>{r.class}</b>{d.deprioritized?.some((x) => x.toLowerCase() === r.class.toLowerCase()) && <> <Pill tone="outline" title="team policy: not counted in findings">de-prioritised</Pill></>}</td><td className="r num">{r.sequences}</td>
                    {(sel.metrics ?? []).slice(0, 5).map((k) => <td key={k} className="r num">{fmtNum(r[k] as number | null)}</td>)}
                    {sel.kind === "detection" && <>
                      <td className="r num">{pct(r.precision)}</td><td className="r num">{pct(r.recall)}</td>
                      <td className="r num">{(r.fp_per_gt ?? 0) > 1 ? <Pill tone="bad">{fmtNum(r.fp_per_gt ?? null)}</Pill> : fmtNum(r.fp_per_gt ?? null)}</td>
                      <td className="r num muted">{r.num_gt}</td><td className="r num muted">{r.num_fp}</td><td className="r num muted">{r.num_fn}</td>
                    </>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {sel && (
        <Card title="Worst sequences" sub={cls ? `class ${cls} (click the class again to clear)` : "all classes — click a class above to filter"} flush right={
          <select className="select" value={m} onChange={(e) => setMetric(e.target.value)}>{(sel.metrics ?? []).map((k) => <option key={k} value={k}>{short(k)}</option>)}</select>
        }>
          <div className="table-wrap" style={{ maxHeight: 520 }}>
            <table className="t">
              <thead><tr><th>Sequence</th><th>Class</th>{worstCols.map((k) => <th key={k} className="r">{short(k).replace(/^num_/, "")}</th>)}</tr></thead>
              <tbody>
                {(d.worst ?? []).map((w, i) => (
                  <tr key={i}><td className="mono">{w.sequence}</td><td>{w.class}</td>{worstCols.map((k) => <td key={k} className="r num">{typeof w[k] === "number" ? fmtNum(w[k] as number) : String(w[k] ?? "")}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
