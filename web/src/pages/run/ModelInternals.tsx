import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChartLine, XCircle } from "lucide-react";
import { api, type AttnTable } from "../../api/client";
import { Card, Loading } from "../../components/ui";
import { fmtNum } from "../../lib/format";

const pct = (v: number | null | undefined): string => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%`);

/** Model internals (from the instrumentation kit): where each decoder layer's cross-attention looks, per modality,
 *  at the end of training (and how it moved since the first quarter, and against the baseline); attention focus;
 *  query statistics (memory share, matched rate per source, query age). */
export function ModelInternals({ runId, baseline, onPlot, stage = "" }: { runId: string; baseline: string | null; onPlot: (keys: string[], together: boolean) => void; stage?: string }) {
  const q = useQuery({ queryKey: ["internals", runId, baseline, stage], queryFn: () => api.internals(runId, baseline, stage || null), staleTime: 60_000 });
  if (q.isLoading) return <Card title="Model internals" fold="metrics.internals"><Loading /></Card>;
  const d = q.data;
  if (!d || !d.logged) {
    return (
      <div className="note" data-testid="internals-missing">
        <span><b>Model internals</b> — not logged by this run. The instrumentation kit records cross-attention per decoder layer × modality and query ages (<code>instrument/README.md</code> → <i>Decoder internals</i>).</span>
      </div>
    );
  }
  const a = d.attention;
  const b = d.baseline?.attention;
  const colKeys = (mo: string): string[] => a.layers.map((l) => a.keys[l]?.[mo]).filter((k): k is string => !!k);
  const rowKeys = (l: string): string[] => a.modalities.map((mo) => a.keys[l]?.[mo]).filter((k): k is string => !!k);
  const bq = new Map((d.baseline?.queries ?? []).map((r) => [r.key, r.end]));
  return (
    <Card title="Model internals" fold="metrics.internals" sub="cross-attention per decoder layer × modality (share of the attention, end of training) · queries" flush>
      <div className="internals" data-testid="model-internals">
        {d.notes.length > 0 && (
          <div className="col" style={{ gap: 4, padding: "10px 14px 0" }}>
            {d.notes.map((n) => (
              <div key={n.id} className="row" style={{ gap: 6, alignItems: "flex-start", fontSize: 12.5 }}>
                {n.status === "bad" ? <XCircle size={14} style={{ color: "var(--bad)", flex: "none", marginTop: 2 }} /> : <AlertTriangle size={14} style={{ color: "var(--warn, #f5b84b)", flex: "none", marginTop: 2 }} />}
                <span><b>{n.text}</b> <span className="faint">{n.check}</span></span>
              </div>
            ))}
          </div>
        )}
        {a.layers.length > 0 && (
          <div style={{ padding: "10px 14px", overflowX: "auto" }}>
            <table className="t compact attn-grid" data-nosort>
              <thead>
                <tr>
                  <th>layer</th>
                  {a.modalities.map((mo) => (
                    <th key={mo} className="r">
                      <button type="button" className="btn ghost sm" title={`Plot ${mo}'s share in every layer over training`} onClick={() => onPlot(colKeys(mo), true)}>{mo} <ChartLine size={11} /></button>
                    </th>
                  ))}
                  <th className="r" title="Normalised entropy: 0 = one token, 1 = uniform">focus (entropy)</th>
                  <th className="r" title="Mean of the largest weight per query">peak</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {a.layers.map((l) => (
                  <tr key={l} data-layer={l}>
                    <td className="mono">{l}</td>
                    {a.modalities.map((mo) => <Cell key={mo} a={a} b={b} l={l} mo={mo} />)}
                    <td className="r num">{fmtNum(a.entropy[l] ?? null)}</td>
                    <td className="r num">{fmtNum(a.top1[l] ?? null)}</td>
                    <td className="act"><button type="button" className="btn ghost sm icon" title={`Plot layer ${l}: every modality's share over training`} aria-label={`Plot layer ${l}`} onClick={() => onPlot(rowKeys(l), true)}><ChartLine size={12} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>Cell: share at the end of training; ↑/↓ since the first quarter; {b ? "Δ in points against the baseline; " : ""}shading = share.</div>
          </div>
        )}
        {d.queries.length > 0 && (
          <div style={{ padding: "0 14px 12px" }}>
            <div className="sub-h">Queries</div>
            <table className="t compact" data-nosort>
              <thead><tr><th>statistic</th><th className="r">first quarter</th><th className="r">end</th>{d.baseline && <th className="r">baseline end</th>}<th /></tr></thead>
              <tbody>
                {d.queries.map((r) => (
                  <tr key={r.key}>
                    <td className="mono">{r.key}</td>
                    <td className="r num">{fmtNum(r.start)}</td>
                    <td className="r num">{fmtNum(r.end)}</td>
                    {d.baseline && <td className="r num">{fmtNum(bq.get(r.key) ?? null)}</td>}
                    <td className="act"><button type="button" className="btn ghost sm icon" aria-label={`Plot ${r.key}`} title="Plot" onClick={() => onPlot([r.key], false)}><ChartLine size={12} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}

function Cell({ a, b, l, mo }: { a: AttnTable; b?: AttnTable; l: string; mo: string }) {
  const v = a.mass[l]?.[mo];
  if (!v) return <td className="r faint">—</td>;
  const end = v.end ?? 0;
  const moved = v.start && v.end !== null ? (v.end - v.start) / v.start : 0;
  const bv = b?.mass[l]?.[mo]?.end;
  const dpts = bv !== undefined && bv !== null && v.end !== null ? (v.end - bv) * 100 : null;
  return (
    <td className="r num attn-cell" style={{ background: `color-mix(in srgb, var(--accent) ${Math.round(Math.min(1, end) * 70)}%, transparent)` }}
      title={`layer ${l} · ${mo}: ${pct(v.end)} at the end, ${pct(v.start)} after a quarter${dpts !== null ? ` · baseline ${pct(bv)}` : ""}`}>
      {pct(v.end)}
      {Math.abs(moved) > 0.2 && <span className="faint"> {moved > 0 ? "↑" : "↓"}</span>}
      {dpts !== null && Math.abs(dpts) >= 1 && <span className="faint" style={{ fontSize: 10 }}> {dpts > 0 ? "+" : ""}{dpts.toFixed(0)}</span>}
    </td>
  );
}
