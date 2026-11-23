import { useQuery } from "@tanstack/react-query";
import { ChartLine, Lightbulb } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import { Card, Loading, Pill, Seg } from "../../components/ui";

const TONE: Record<string, string> = { behind: "bad", crossed: "warn", ahead: "good", closed: "info", same: "outline", insufficient: "outline" };
const WORD: Record<string, string> = { behind: "higher", ahead: "lower", crossed: "crossed", closed: "closed again", same: "same", insufficient: "too few points" };

/** How every loss the two runs share *progresses* differently — divergence point, outcome, shape — and what the
 *  pattern says about the training. */
export function LossDrift({ runId, baseline, onPlot }: { runId: string; baseline: string; onPlot: (keys: string[]) => void }) {
  const q = useQuery({ queryKey: ["loss-drift", baseline, runId], queryFn: () => api.lossDrift(baseline, runId), staleTime: 60_000 });
  const [show, setShow] = useState<"moved" | "all">("moved");
  const rows = (q.data?.rows ?? []).filter((r) => show === "all" || !["same", "insufficient"].includes(r.outcome) || r.shape_notes.length > 0);
  return (
    <Card title="Loss drift vs the baseline" fold="metrics.drift" flush
      sub={q.data ? `${q.data.summary} · aligned by ${q.data.aligned_by}` : "how each shared loss progresses, not only where it ends"}
      right={<Seg options={[{ id: "moved", label: "Drifting" }, { id: "all", label: "All" }]} value={show} onChange={setShow} />}>
      {q.isLoading ? <Loading label="Comparing the loss progressions…" /> : q.error ? <div className="faint" style={{ padding: 12 }}>{String(q.error)}</div> : (
        <div data-testid="loss-drift">
          {q.data!.readings.length > 0 && (
            <div className="col drift-readings">
              {q.data!.readings.map((r) => <div key={r} className="row" style={{ gap: 6, alignItems: "flex-start" }}><Lightbulb size={14} style={{ flex: "none", marginTop: 2, color: "var(--ai, var(--accent))" }} /><span>{r}</span></div>)}
            </div>
          )}
          <table className="t compact" data-nosort>
            <thead><tr><th>loss</th><th>vs baseline</th><th className="r">drifts from</th><th className="r">at the end</th><th>shape</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} data-drift={r.outcome}>
                  <td className="mono trunc" style={{ maxWidth: 280 }} title={r.sentence}>{r.key}</td>
                  <td><Pill tone={TONE[r.outcome]}>{WORD[r.outcome] ?? r.outcome}</Pill></td>
                  <td className="r num">{r.diverge_fraction === null ? "—" : `${(r.diverge_fraction * 100).toFixed(0)}%`}</td>
                  <td className="r num" title="+ = lower (better) than the baseline">{r.final_gap === null ? "" : `${r.final_gap > 0 ? "+" : ""}${(r.final_gap * 100).toFixed(1)}%`}</td>
                  <td className="faint" style={{ fontSize: 12 }}>{[...r.shape_notes, r.lr_note].filter(Boolean).join(" · ")}</td>
                  <td className="act"><button type="button" className="btn ghost sm icon" aria-label={`Plot ${r.key}`} title="Plot against the baseline" onClick={() => onPlot([r.key])}><ChartLine size={12} /></button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={6} className="faint">No loss drifts — show all to see every loss.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
