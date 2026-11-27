import { fmtNum } from "../lib/format";
import { useQuery } from "@tanstack/react-query";
import { BookmarkPlus, Bot, Loader2, Plus, X } from "lucide-react";
import { Link } from "react-router-dom";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { ChartAnswer, CurvesComparison, CurvesKey, DivergenceRow } from "../api/types";
import { Chart, PALETTE, type ChartMarker, type ChartSeries } from "./Chart";
import { Answer } from "./ChartInsight";
import { RunPicker } from "./RunPicker";
import { Card, ErrorBox, Loading, Pill } from "./ui";

const OUTCOME: Record<DivergenceRow["outcome"], { tone: string; label: string }> = {
  same: { tone: "outline", label: "same (within noise)" },
  ahead: { tone: "good", label: "ahead" },
  behind: { tone: "bad", label: "behind" },
  closed: { tone: "warn", label: "gap closed" },
  crossed: { tone: "warn", label: "crossed" },
  insufficient: { tone: "outline", label: "too few points" },
};
const pct = (x: number | null): string => (x == null ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`);

/** Where and why 2–10 runs diverge from the reference (the first run), metric by metric. */
export function CompareCurves({ runs, onAdd, onRemove, stages = {}, onStage, stage }: {
  runs: string[]; onAdd?: (r: string) => void; onRemove?: (r: string) => void;
  stages?: Record<string, string>; onStage?: (run: string, stage: string) => void;
  stage?: string;  // the page's stage: every run that has it (a per-run choice wins)
}) {
  const q = useQuery({ queryKey: ["compare-curves", runs, stages, stage], queryFn: () => api.compareCurves(runs, undefined, stages, stage), enabled: runs.length >= 2 });
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<ChartAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const ask = () => { setAsking(true); api.compareAsk(runs, question).then(setAnswer).finally(() => setAsking(false)); };
  const studies = useQuery({ queryKey: ["studies"], queryFn: api.studies });
  const [target, setTarget] = useState("");
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const save = (): void => {
    if (!target) return;
    void api.studyAddComparison(target, runs, note, stages).then(() => { setSaved(target); setNote(""); });
  };
  const color = useMemo(() => Object.fromEntries((q.data?.runs ?? []).map((r, i) => [r.label, PALETTE[i % PALETTE.length]!])), [q.data]);
  return (
    <Card title="Curves — where and why the runs diverge" sub={`reference: ${q.data?.reference ?? runs[0]?.split(":").pop() ?? ""} · ${runs.length} runs${q.data?.reference_seeds?.length ? ` · noise floor from ${q.data.reference_seeds.length + 1} reference seeds` : ""}`}>
      <div className="row wrap" style={{ gap: 6, marginBottom: 10 }}>
        {(q.data?.runs ?? runs.map((r): CurvesComparison["runs"][number] => ({ ref: r, label: r.split(":").pop()!, status: "" }))).map((r, i) => (
          <span key={r.ref} className="chip" style={{ borderColor: color[r.label] }}>
            <i className="ci-dot" style={{ background: color[r.label] ?? "#888" }} />
            {r.alias && <b className="faint">{r.alias}</b>}
            <span className="mono">{r.label}</span>{i === 0 && <span className="faint"> · reference</span>}
            {onStage && (r.stages?.length ?? 0) > 0 && (
              <select className="select stage-pick" title="Which pipeline stage's metrics to compare (stages are never matched by name)" value={stages[r.ref] ?? ""} onChange={(e) => onStage(r.ref, e.target.value)}>
                <option value="">headline (last stage)</option>
                {r.stages!.map((st) => <option key={st} value={st}>{st}</option>)}
              </select>
            )}
            {r.status === "running" && <span className="faint"> · running</span>}
            {i >= 2 && onRemove && <button className="btn ghost sm icon" title="Remove" onClick={() => onRemove(r.ref)}><X size={12} /></button>}
          </span>
        ))}
        {onAdd && runs.length < 10 && <span className="row" style={{ gap: 4 }}><Plus size={13} className="faint" /><RunPicker value={null} onChange={(v) => v && !runs.includes(v) && onAdd(v)} placeholder="Add a run…" width={220} /></span>}
      </div>
      {q.isLoading ? <Loading label="Aligning curves…" /> : q.error ? <ErrorBox error={q.error} /> : q.data && (
        <div className="col" style={{ gap: 16 }}>
          {q.data.keys.map((k) => <KeyBlock key={k.key} k={k} color={color} />)}
          <div className="row" style={{ gap: 6 }}>
            <input className="input" placeholder="Ask AI about this comparison — e.g. what explains the gap? which change should I test?" value={question}
              onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") ask(); }} />
            <button className="btn sm" onClick={ask} disabled={asking}>{asking ? <Loader2 size={13} className="spin" /> : <Bot size={13} />} Ask</button>
          </div>
          {answer && <Answer a={answer} />}
          <div className="save-cmp row wrap" style={{ gap: 6 }}>
            <BookmarkPlus size={14} className="faint" /><span className="faint">Keep this comparison as study evidence:</span>
            <select className="select" aria-label="Compare against" value={target} onChange={(e) => { setTarget(e.target.value); setSaved(null); }}>
              <option value="">choose a study…</option>
              {(studies.data ?? []).filter((s) => s.status !== "closed").map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
            <input className="input" style={{ width: 280 }} placeholder="Optional note (why this comparison matters)" value={note} onChange={(e) => setNote(e.target.value)} />
            <button className="btn sm" disabled={!target} onClick={save}>Save to study</button>
            {saved && <Link to={`/studies/${saved}`} className="faint">Saved — open the study →</Link>}
          </div>
        </div>
      )}
    </Card>
  );
}

function KeyBlock({ k, color }: { k: CurvesKey; color: Record<string, string> }) {
  const series: ChartSeries[] = k.series.map((s) => ({ name: s.run, x: s.steps, y: s.values, color: color[s.run], dash: s.run === k.reference }));
  // One exploding run (divergence, NaN) flattens all others: use a log scale when the ranges differ by > 5×.
  const peaks = k.series.map((s) => Math.max(...s.values.filter((v): v is number => v != null && Number.isFinite(v) && v > 0), 0)).filter((v) => v > 0).sort((a, b) => a - b);
  const autoLog = peaks.length >= 2 && peaks[peaks.length - 1]! / peaks[Math.floor((peaks.length - 1) / 2)]! > 5;
  const markers: ChartMarker[] = k.rows.filter((r) => r.diverge_step != null && r.outcome !== "same")
    .map((r) => ({ x: r.diverge_step!, label: `${r.run} diverges`, color: color[r.run] }));
  // logged once per run (an evaluation stage): a value per run, drawn as bars — a one-point line says nothing
  const finite = (v: (number | null)[]): number[] => v.filter((x): x is number => x != null && Number.isFinite(x));
  const single = k.series.length > 0 && k.series.every((s) => finite(s.values).length <= 2);
  const vals = k.series.map((s) => ({ run: s.run, v: finite(s.values).pop() ?? null }));
  const top = Math.max(...vals.map((x) => Math.abs(x.v ?? 0)), 1e-12);
  const ref = vals.find((x) => x.run === k.reference)?.v ?? null;
  return (
    <div className="cmp-key">
      {single ? (
        <div className="card" style={{ padding: "10px 12px" }} data-testid="cmp-values" data-key={k.key}>
          <div className="row" style={{ marginBottom: 6 }}><b className="mono" style={{ fontSize: 12.5 }}>{k.key}</b><span className="faint" style={{ fontSize: 11 }}>· one value per run (evaluation) — bars, not a curve</span></div>
          <table className="t compact value-bars" data-nosort><tbody>{vals.map((x) => (
            <tr key={x.run}>
              <td className="mono nowrap trunc" style={{ width: 220, maxWidth: 220 }}>{x.run}{x.run === k.reference ? " (reference)" : ""}</td>
              <td style={{ minWidth: 120 }}><div className="value-bar" style={{ width: `${Math.max(2, (Math.abs(x.v ?? 0) / top) * 100)}%`, background: color[x.run] ?? "#888", opacity: x.run === k.reference ? 0.55 : 0.9 }} /></td>
              <td className="r num" style={{ width: 90 }}>{x.v == null ? "—" : fmtNum(x.v)}</td>
              <td className="r num faint" style={{ width: 110 }}>{x.run !== k.reference && x.v != null && ref != null ? pct((x.v - ref) / (Math.abs(ref) || 1e-12)) : ""}</td>
            </tr>
          ))}</tbody></table>
        </div>
      ) : <Chart title={k.key} series={series} markers={markers} smoothing={k.key.startsWith("train/") ? 0.6 : 0} showRaw={false} height={240} logY={autoLog} />}
      {autoLog && !single && <div className="faint" style={{ fontSize: 11 }}>Log scale: one run's values are more than 5× the others.</div>}
      <div className="faint" style={{ margin: "4px 2px 6px" }}>{k.summary} {k.direction !== "unknown" && <span>({k.direction} is better)</span>}</div>
      <div className="table-wrap">
        <table className="t compact">
          <thead><tr><th>Run</th><th>Verdict</th><th className="r">Diverges at</th><th className="r">Final gap</th><th title="Final value against the spread of the reference run's seed replicates (same group tag)">vs seed noise</th><th>Reading</th><th>Fits the timing (hypotheses)</th></tr></thead>
          <tbody>{k.rows.map((r) => (
            <tr key={r.run}>
              <td className="mono"><i className="ci-dot" style={{ background: color[r.run] ?? "#888", marginRight: 6 }} />{r.run}</td>
              <td><Pill tone={OUTCOME[r.outcome].tone}>{OUTCOME[r.outcome].label}</Pill></td>
              <td className="r num">{r.diverge_step == null ? "—" : `${Math.round(r.diverge_step).toLocaleString()}${r.diverge_fraction != null ? ` (${Math.round(r.diverge_fraction * 100)}%)` : ""}`}</td>
              <td className="r num" style={{ color: r.outcome === "behind" ? "var(--bad)" : r.outcome === "ahead" ? "var(--good)" : undefined }}>{pct(r.final_gap)}</td>
              <td>{r.seed_verdict ? <Pill tone={r.seed_verdict.startsWith("beyond") ? (r.seed_z! > 0 ? "good" : "bad") : "outline"} title={`${r.seed_z?.toFixed(1)}σ against ${r.seed_n} seeds`}>{r.seed_verdict.replace(" seed noise", "")} ({r.seed_z! >= 0 ? "+" : ""}{r.seed_z?.toFixed(1)}σ)</Pill> : <span className="faint" title="Tag seed replicates of the reference with the same group tag">no seeds</span>}</td>
              <td className="wrap" style={{ minWidth: 280 }}>{r.sentence}{r.lr_note && <div className="faint">LR: {r.lr_note}</div>}</td>
              <td className="wrap mono" style={{ fontSize: 11.5, minWidth: 200 }}>{r.outcome === "same" || r.outcome === "insufficient" ? "—" : r.fits.map((f) => <div key={f}>{f}</div>)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}
