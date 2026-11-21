import { useMutation } from "@tanstack/react-query";
import { Cpu, Lightbulb, Loader2, Pin, PinOff } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, refStr } from "../../api/client";
import type { AnalysisResult, Finding, MetricDelta, StudyEvidence } from "../../api/types";
import { FindingCard, sortFindings } from "../../components/FindingCard";
import { Card, Empty, Pill, Switch } from "../../components/ui";
import { fmtNum, fmtPct, fmtValue } from "../../lib/format";
import type { HypothesisDraft } from "./HypothesisDialog";

const ARCH = ["module_health", "modality_ablation", "feature_health", "decoder_layers"];
const short = (r: string): string => r.split(":").slice(1).join(":") || r;

export function EvidenceTab({ ev, pinned, onPin, onHypothesis }: {
  ev: StudyEvidence; pinned: string[]; onPin: (fid: string, pinned: boolean) => void; onHypothesis: (d: HypothesisDraft) => void;
}) {
  const [slices, setSlices] = useState(false);
  const [full, setFull] = useState(false);
  const [results, setResults] = useState<Record<string, AnalysisResult[]>>({});
  const base = ev.study.baseline ? refStr(ev.study.baseline) : null;
  const rows = useMemo(() => [...ev.runs].sort((a, b) => Number(b.is_baseline) - Number(a.is_baseline)), [ev.runs]);
  const analyze = useMutation({
    mutationFn: async () => {
      const out: Record<string, AnalysisResult[]> = {};
      for (const r of rows) {
        const id = refStr(r.run.ref);
        out[id] = await api.analyze(id, !r.is_baseline ? base : null, full ? undefined : ARCH);
      }
      return out;
    },
    onSuccess: setResults,
  });

  const metrics = useMemo(() => {
    const keys = new Set<string>();
    rows.forEach((r) => Object.keys(r.summary).forEach((k) => (slices || !k.includes("[")) && !k.includes("ablation=") && keys.add(k)));
    return [...keys].sort((a, b) => Number(a.includes("[")) - Number(b.includes("[")) || a.localeCompare(b));
  }, [rows, slices]);
  const delta = (row: (typeof rows)[number], k: string): MetricDelta | undefined => row.deltas.find((d) => d.metric === k);

  const findingActions = (f: Finding, run: string) => (
    <>
      <button className="btn ghost sm" title={pinned.includes(f.id) ? "Unpin" : "Pin to study"} onClick={() => onPin(f.id, !pinned.includes(f.id))}>
        {pinned.includes(f.id) ? <PinOff size={12} /> : <Pin size={12} />}
      </button>
      <button className="btn sm" title="Turn into a hypothesis" onClick={() => onHypothesis({
        statement: f.hypotheses[0]?.statement ?? f.title, rationale: f.statement,
        proposed_change: f.recommendations[0] ? `${f.recommendations[0].title} (${f.recommendations[0].controlled_variable})` : "",
        evidence: [{ kind: "finding", ref: f.id, label: f.title, run: null }], source: `finding:${f.id}`, reference_run: run,
      })}><Lightbulb size={12} /> Hypothesis</button>
    </>
  );

  return (
    <div className="col" style={{ gap: 14 }}>
      <Card title="Metrics" sub={base ? `deltas vs ${short(base)} — coloured only beyond seed noise` : "set a baseline to see deltas"}
        right={<span className="row muted" style={{ fontSize: 12 }}>Slices <Switch on={slices} onChange={setSlices} /></span>} flush>
        {metrics.length === 0 ? <Empty title="No evaluation metrics logged yet" /> : (
          <div className="table-wrap" style={{ maxHeight: 460 }}>
            <table className="t compact">
              <thead><tr><th>Metric</th>{rows.map((r) => <th key={refStr(r.run.ref)} className="r" style={{ textTransform: "none" }}>
                <Link to={`/runs/${encodeURIComponent(refStr(r.run.ref))}`}>{r.run.name}</Link>{r.is_baseline && <span className="faint"> · baseline</span>}</th>)}</tr></thead>
              <tbody>
                {metrics.map((k) => (
                  <tr key={k}>
                    <td className="mono">{k}</td>
                    {rows.map((r) => {
                      const d = delta(r, k);
                      const color = d?.verdict === "regressed" ? "var(--bad)" : d?.verdict === "improved" ? "var(--good)" : undefined;
                      return (
                        <td key={refStr(r.run.ref)} className="r num">
                          {fmtNum(r.summary[k])}
                          {d && <span style={{ color: color ?? "var(--text-3)", marginLeft: 6, fontSize: 11.5 }}>{fmtPct(d.relative)}</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={<span className="row"><Cpu size={15} color="var(--accent-2)" /> Architecture diagnostics</span>}
        sub="module learning health · modality contribution · feature maps · decoder depth"
        right={<>
          <span className="row muted" style={{ fontSize: 12 }}>All analyses <Switch on={full} onChange={setFull} /></span>
          <button className="btn primary sm" disabled={analyze.isPending} onClick={() => analyze.mutate()}>{analyze.isPending ? <Loader2 size={13} className="spin" /> : <Cpu size={13} />} Run</button>
        </>}>
        {Object.keys(results).length === 0 ? (
          <div className="faint">Runs the architecture analyses on every run in the study. They need metrics from the instrumentation kit
            (per-module gradients/updates, feature statistics, per-layer losses, evaluation with a branch zeroed) — runs without them are listed as skipped.</div>
        ) : (
          <div className="col" style={{ gap: 16 }}>
            {rows.map((r) => {
              const id = refStr(r.run.ref);
              const res = results[id] ?? [];
              const fs = sortFindings(res.flatMap((x) => x.findings)).filter((f) => f.severity !== "info" || f.category === "architecture");
              const skipped = res.filter((x) => x.skipped_reason);
              return (
                <div key={id}>
                  <div className="row" style={{ marginBottom: 8 }}>
                    <b>{r.run.name}</b>{r.is_baseline && <Pill tone="outline">baseline</Pill>}
                    <span className="faint" style={{ fontSize: 12 }}>{fs.length} findings{skipped.length ? ` · ${skipped.length} analyses skipped (metrics not logged)` : ""}</span>
                  </div>
                  {fs.length === 0 ? <div className="faint" style={{ fontSize: 12 }}>Nothing notable.</div> : fs.map((f) => <FindingCard key={f.id} f={f} actions={findingActions(f, id)} />)}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {rows.some((r) => r.config_changes.length) && (
        <Card title="What differs from the baseline" flush>
          <table className="t compact">
            <tbody>
              {rows.filter((r) => r.config_changes.length).flatMap((r) => r.config_changes.map((c) => (
                <tr key={refStr(r.run.ref) + c.path}>
                  <td style={{ width: 180 }}>{r.run.name}</td>
                  <td className="mono wrap">{c.path}</td>
                  <td className="mono wrap faint">{c.kind === "added" ? "—" : fmtValue(c.baseline)}</td>
                  <td className="mono wrap">{c.kind === "removed" ? "—" : fmtValue(c.candidate)}</td>
                  <td><span className="tag">{c.category}</span></td>
                </tr>
              )))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
