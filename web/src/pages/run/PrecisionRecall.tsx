import { useQuery } from "@tanstack/react-query";
import { PendingDownload } from "../../components/PendingDownload";
import { Eye, FileSearch, Info, Loader2, Search, Target, X } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../../api/client";
import type { EvalFile, WorkingPoint } from "../../api/types";
import { Card, Empty, ErrorBox, Loading, Pill, Seg } from "../../components/ui";
import { fmtBytes } from "../../lib/format";

const f3 = (x: number | null | undefined): string => (x == null || !Number.isFinite(x) ? "—" : x.toFixed(3));
const heat = (v: number): string => `hsla(${Math.round(120 * Math.max(0, Math.min(1, v)))}, 65%, 45%, 0.35)`;

/** Evidence-first precision & recall: logged working points (target ≠ measured recall), PR-AUC by distance, counts,
 *  log lines and the evaluation files with what each can and cannot show. */
export function PrecisionRecallTab({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["pr", runId], queryFn: () => api.precisionRecall(runId) });
  const files = useQuery({ queryKey: ["eval-files", runId], queryFn: () => api.evalFiles(runId) });
  const [stage, setStage] = useState<string>("");
  const [cls, setCls] = useState<string>("");
  const [recall, setRecall] = useState<number | null>(null);
  const [logs, setLogs] = useState(false);
  const logQ = useQuery({ queryKey: ["pr-logs", runId], queryFn: () => api.precisionRecallLogs(runId), enabled: logs });
  const d = q.data;
  const st = stage || d?.stages[d.stages.length - 1] || "";
  const recalls = useMemo(() => [...new Set((d?.auc ?? []).map((a) => a.target_recall))].sort(), [d]);
  const rec = recall ?? recalls[recalls.length - 1] ?? null;
  if (q.isLoading) return <Loading label="Reading evaluation metrics…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  if (!d) return null;
  const inStage = <T extends { stage: string; class: string }>(rows: T[]): T[] => rows.filter((r) => r.stage === st && (!cls || r.class === cls));
  const wps = inStage(d.working_points);
  const aucs = d.auc.filter((a) => a.stage === st && a.target_recall === rec);
  const dists = [...new Set(aucs.map((a) => a.distance_m))].sort((a, b) => a - b);
  const counts = inStage(d.counts);
  const depr = new Set(d.deprioritized);
  return (
    <div className="col" style={{ gap: 12 }}>
      <PendingDownload run={runId} pending={d.pending} />
      <div className="card row wrap" style={{ padding: "8px 12px", gap: 14 }}>
        {d.stages.length > 1 && <span className="row muted">Stage <Seg options={d.stages.map((s) => ({ id: s, label: s }))} value={st} onChange={setStage} /></span>}
        <label className="row muted" style={{ gap: 6 }}>Class
          <select className="select" value={cls} onChange={(e) => setCls(e.target.value)}>
            <option value="">all classes</option>
            {d.classes.map((c) => <option key={c} value={c}>{c}{depr.has(c) ? " (deprioritised)" : ""}</option>)}
          </select>
        </label>
        {recalls.length > 1 && <label className="row muted" style={{ gap: 6 }}>AUC at target recall
          <select className="select" value={rec ?? ""} onChange={(e) => setRecall(Number(e.target.value))}>{recalls.map((r) => <option key={r} value={r}>{r}</option>)}</select></label>}
      </div>
      {d.notes.map((n) => <div key={n} className="note"><Info size={14} />{n}</div>)}
      <details className="faint" style={{ fontSize: 12 }}><summary>How to read this page</summary><ul style={{ margin: "4px 0 0 18px", padding: 0 }}>{d.caveats.map((c) => <li key={c}>{c}</li>)}</ul></details>

      <Card title={<span className="row" style={{ gap: 6 }}><Target size={15} /> Working points</span>} sub="as logged — target recall from the metric name, measured recall from its value" flush>
        {wps.length === 0 ? <div className="faint" style={{ padding: 14 }}>No working-point metrics for this stage.</div> : (
          <div className="table-wrap"><table className="t compact">
            <thead><tr><th>Class</th><th className="r">Target recall</th><th className="r">Measured recall</th><th style={{ width: 160 }}>Shortfall</th><th className="r">Precision</th><th className="r">F1</th></tr></thead>
            <tbody>{wps.map((w: WorkingPoint) => (
              <tr key={`${w.class}-${w.target_recall}`}>
                <td>{w.class}{depr.has(w.class) && <span className="faint"> · deprioritised</span>}</td>
                <td className="r num faint">{w.target_recall}</td><td className="r num">{f3(w.recall)}</td>
                <td>{(w.shortfall ?? 0) > 0.005 ? <span className="row" style={{ gap: 6 }}><span className="bar bad" style={{ width: `${Math.min(100, (w.shortfall ?? 0) * 400)}%` }} /><span className="num" style={{ color: "var(--bad)" }}>−{f3(w.shortfall)}</span></span> : <span className="faint">meets target</span>}</td>
                <td className="r num">{f3(w.precision)}</td><td className="r num">{f3(w.f1)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>

      <Card title="PR-curve AUC by distance" sub={rec != null ? `target recall ${rec} · class × distance (m)` : undefined} flush>
        {aucs.length === 0 ? <div className="faint" style={{ padding: 14 }}>No distance-stratified PR-curve AUC for this stage.</div> : (
          <div className="table-wrap"><table className="t compact">
            <thead><tr><th>Class</th>{dists.map((x) => <th key={x} className="r">≤{x} m</th>)}</tr></thead>
            <tbody>{[...new Set(aucs.map((a) => a.class))].filter((c) => !cls || c === cls).sort((a, b) => d.classes.indexOf(a) - d.classes.indexOf(b)).map((c) => (
              <tr key={c}><td>{c}{depr.has(c) && <span className="faint"> · deprioritised</span>}</td>{dists.map((x) => {
                const v = aucs.find((a) => a.class === c && a.distance_m === x)?.auc;
                return <td key={x} className="r num" style={{ background: v == null ? undefined : heat(v) }}>{f3(v)}</td>;
              })}</tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>

      {counts.length > 0 && (
        <Card title="Counts from the per-sequence tables" sub="precision / recall derived from TP / FP / FN / GT — a different threshold than the working points" flush>
          <div className="table-wrap"><table className="t compact">
            <thead><tr><th>Class</th><th className="r">Sequences</th><th className="r">TP</th><th className="r">FP</th><th className="r">FN</th><th className="r">GT</th><th className="r">Precision</th><th className="r">Recall</th><th className="r">FP / GT</th></tr></thead>
            <tbody>{counts.map((c) => (
              <tr key={c.class}><td>{c.class}</td><td className="r num">{c.sequences}</td><td className="r num">{c.num_tp}</td><td className="r num">{c.num_fp}</td><td className="r num">{c.num_fn}</td><td className="r num">{c.num_gt}</td>
                <td className="r num">{f3(c.precision)}</td><td className="r num">{f3(c.recall)}</td><td className="r num">{f3(c.fp_per_gt)}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
      )}

      <Card title={<span className="row" style={{ gap: 6 }}><Search size={15} /> Precision / recall / calibration lines in the logs</span>}
        right={!logs ? <button className="btn sm" onClick={() => setLogs(true)}>Search all logs</button> : undefined} flush>
        {!logs ? <div className="faint" style={{ padding: 14 }}>Scans every log of the run and its stages (downloaded into the cache once).</div>
          : logQ.isLoading ? <Loading label="Scanning logs…" /> : logQ.error ? <ErrorBox error={logQ.error} /> : (
            <div className="table-wrap" style={{ maxHeight: 320 }}>
              {(logQ.data?.lines ?? []).length === 0 ? <div className="faint" style={{ padding: 14 }}>No matching lines.</div> : (
                <table className="t compact"><tbody>{logQ.data!.lines.map((l, i) => (
                  <tr key={i}><td className="mono faint" style={{ whiteSpace: "nowrap" }}>{l.stage || "run"} · {l.path.split("/").pop()}:{l.line_no}</td><td className="mono wrap">{l.text}</td></tr>
                ))}</tbody></table>
              )}
            </div>
          )}
      </Card>

      <EvalFiles runId={runId} files={files.data ?? []} loading={files.isLoading} stage={st} cls={cls} />
    </div>
  );
}

function EvalFiles({ runId, files, loading, stage, cls }: { runId: string; files: EvalFile[]; loading: boolean; stage: string; cls: string }) {
  const [open, setOpen] = useState<EvalFile | null>(null);
  const [filter, setFilter] = useState("");
  const shown = files.filter((f) => (!stage || !f.stage || f.stage === stage) && (!cls || !f.class || f.class === cls) && (!filter || f.path.toLowerCase().includes(filter.toLowerCase())));
  const families = [...new Set(shown.map((f) => f.family))];
  const preview = useQuery({ queryKey: ["preview", runId, open?.ref], queryFn: () => api.preview(runId, open!.ref), enabled: !!open });
  return (
    <Card title={<span className="row" style={{ gap: 6 }}><FileSearch size={15} /> Evaluation files</span>} sub={`${shown.length} of ${files.length}`}
      right={<input className="input" style={{ width: 220 }} placeholder="Filter path" value={filter} onChange={(e) => setFilter(e.target.value)} />} flush>
      {loading ? <Loading /> : files.length === 0 ? <Empty title="No evaluation files">This run has no files under evaluation/, summaries/ or visualizations/.</Empty> : (
        <div className="col" style={{ gap: 10, padding: 12 }}>
          {families.map((fam, i) => {
            const fs = shown.filter((f) => f.family === fam);
            return (
              <details key={fam} open={i === 0 || families.length <= 3}>
                <summary><b>{fam}</b> <span className="faint">· {fs.length} file(s) — {fs[0]!.purpose}</span></summary>
                <div className="faint" style={{ fontSize: 12, margin: "4px 0 6px" }}>Caveat: {fs[0]!.caveat}</div>
                <table className="t compact"><tbody>{fs.map((f) => (
                  <tr key={f.ref}>
                    <td className="mono ellipsis" style={{ maxWidth: 620 }} title={f.path}>{f.path.split("/").slice(-3).join("/")}</td>
                    <td>{f.class && <span className="tag">{f.class}</span>}</td><td className="faint">{f.stage}</td>
                    <td className="r num faint">{f.size_bytes != null ? fmtBytes(f.size_bytes) : ""}</td>
                    <td>{f.cached && <Pill tone="good">cached</Pill>}</td>
                    <td><button className="btn ghost sm" onClick={() => setOpen(f)}><Eye size={13} /> Preview</button></td>
                  </tr>
                ))}</tbody></table>
              </details>
            );
          })}
        </div>
      )}
      {open && (
        <div className="preview-pane">
          <div className="row" style={{ gap: 6 }}><b className="mono ellipsis">{open.path.split("/").slice(-3).join("/")}</b><span className="spacer" />
            <button className="btn ghost sm icon" onClick={() => setOpen(null)}><X size={14} /></button></div>
          <div className="faint" style={{ fontSize: 12 }}>{open.family}: {open.purpose}</div>
          {preview.isLoading ? <div className="row"><Loader2 size={14} className="spin" /> fetching…</div> : preview.error ? <ErrorBox error={preview.error} /> : preview.data && (
            preview.data.kind === "text" ? <pre className="preview-text">{preview.data.text}{preview.data.truncated ? "\n… (truncated)" : ""}</pre>
              : preview.data.kind === "image" ? <img src={preview.data.url} alt={open.path} style={{ maxWidth: "100%" }} />
              : preview.data.kind === "table" ? (
                <div className="table-wrap" style={{ maxHeight: 360 }}><table className="t compact">
                  <thead><tr>{preview.data.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                  <tbody>{preview.data.rows.map((r, i) => <tr key={i}>{(preview.data as { columns: string[] }).columns.map((c) => <td key={c} className="mono">{String(r[c] ?? "")}</td>)}</tr>)}</tbody>
                </table><div className="faint">{preview.data.rows.length} of {preview.data.total_rows} rows</div></div>
              ) : <div className="faint">{preview.data.kind === "too_large" ? "Too large to preview — use Save on the Artifacts tab." : preview.data.kind === "binary" ? "Binary file — not previewable." : preview.data.note}</div>
          )}
        </div>
      )}
    </Card>
  );
}
