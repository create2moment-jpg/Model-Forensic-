import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AnalysisResult, Finding, PlotSpec } from "../../api/types";
import { Chart } from "../../components/Chart";
import { FindingCard, SEV_ORDER, sortFindings } from "../../components/FindingCard";
import { Card, Empty, ErrorBox, Pill } from "../../components/ui";

export function FindingsTab({ runId, baseline, autoRun, stored }: { runId: string; baseline: string | null; autoRun: boolean; stored: Finding[] }) {
  const catalog = useQuery({ queryKey: ["analyses"], queryFn: api.analyses });
  const [chosen, setChosen] = useState<string[]>([]);
  const [useBaseline, setUseBaseline] = useState(true);
  const [sev, setSev] = useState<string>("all");
  const qc = useQueryClient();
  const m = useMutation({ mutationFn: () => api.analyze(runId, useBaseline ? baseline : null, chosen), onSuccess: () => void qc.invalidateQueries({ queryKey: ["overview", runId] }) });
  useEffect(() => { if (autoRun && !m.data && !m.isPending) m.mutate(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [autoRun]);

  const results: AnalysisResult[] | undefined = m.data;
  const findings = sortFindings(results ? results.flatMap((r) => r.findings) : stored).filter((f) => sev === "all" || SEV_ORDER[f.severity] >= SEV_ORDER[sev as keyof typeof SEV_ORDER]);
  const plots: PlotSpec[] = results?.flatMap((r) => r.plots) ?? [];

  return (
    <div className="split">
      <div className="col" style={{ gap: 14 }}>
        <div className="row wrap">
          <span className="muted">Minimum severity</span>
          {["all", "low", "medium", "high", "critical"].map((s) => <button key={s} className={`btn sm ${sev === s ? "primary" : ""}`} onClick={() => setSev(s)}>{s}</button>)}
          <span className="spacer" />
          {!results && stored.length > 0 && <span className="faint">Showing stored findings — run analyses to refresh.</span>}
        </div>
        {m.error && <ErrorBox error={m.error} />}
        {m.isPending ? <Card><div className="row muted"><Loader2 size={15} className="spin" /> Running deterministic analyses…</div></Card> :
          findings.length === 0 ? <Card><Empty title={results ? "No findings above threshold" : "No analyses run yet"} action={!results ? <button className="btn primary" onClick={() => m.mutate()}><Play size={14} /> Run all analyses</button> : undefined}>
            {results ? "The selected analyses found nothing suspicious. Check skipped analyses on the right." : "Analyses are deterministic and reproducible; findings carry evidence, hypotheses and recommended experiments."}
          </Empty></Card> : findings.map((f, i) => <FindingCard key={f.id} f={f} defaultOpen={i === 0} />)}
        {plots.length > 0 && (
          <div className="grid g2">
            {plots.map((p) => <PlotCard key={p.id} p={p} />)}
          </div>
        )}
      </div>
      <div className="col" style={{ gap: 14 }}>
        <Card title="Analyses" right={<button className="btn primary sm" disabled={m.isPending} onClick={() => m.mutate()}>{m.isPending ? <Loader2 size={13} className="spin" /> : <Play size={13} />} Run</button>}>
          <div className="col" style={{ gap: 8 }}>
            {baseline && (
              <label className="row muted"><input type="checkbox" checked={useBaseline} onChange={(e) => setUseBaseline(e.target.checked)} style={{ accentColor: "var(--accent)" }} />Compare against baseline <span className="mono faint ellipsis">{baseline.split(":").pop()}</span></label>
            )}
            <div className="sep" style={{ margin: "4px 0" }} />
            {(catalog.data ?? []).map((a) => {
              const res = results?.find((r) => r.analysis === a.name);
              const needsBaseline = a.requires.includes("baseline");
              return (
                <label key={a.name} className="row" style={{ alignItems: "flex-start", cursor: "pointer" }}>
                  <input type="checkbox" style={{ accentColor: "var(--accent)", marginTop: 3 }} checked={chosen.length === 0 || chosen.includes(a.name)}
                    onChange={() => setChosen((c) => { const base = c.length === 0 ? (catalog.data ?? []).map((x) => x.name) : c; return base.includes(a.name) ? base.filter((x) => x !== a.name) : [...base, a.name]; })} />
                  <div style={{ flex: 1 }}>
                    <div className="row"><b style={{ fontWeight: 550 }}>{a.title}</b>{needsBaseline && <Pill tone="outline">baseline</Pill>}
                      {res && (res.skipped_reason ? <Pill tone="outline">skipped</Pill> : <Pill tone={res.findings.length ? "warn" : "good"}>{res.findings.length}</Pill>)}</div>
                    <div className="faint" style={{ fontSize: 12 }}>{res?.skipped_reason ?? a.description}</div>
                    {res?.notes.map((n) => <div key={n} className="faint" style={{ fontSize: 12 }}>• {n}</div>)}
                  </div>
                </label>
              );
            })}
          </div>
        </Card>
      </div>
    </div>
  );
}

function PlotCard({ p }: { p: PlotSpec }) {
  if (p.kind === "bar") {
    const pts = p.series[0]?.points ?? [];
    const max = Math.max(...pts.map(([, v]) => Math.abs(v)), 1e-12);
    return (
      <Card title={p.title}>
        <div className="col" style={{ gap: 6 }}>
          {pts.map(([k, v]) => (
            <div key={String(k)} className="row">
              <span className="mono ellipsis" style={{ width: 170 }}>{String(k)}</span>
              <div style={{ flex: 1, height: 8, background: "var(--bg-3)", borderRadius: 4 }}>
                <div style={{ width: `${(Math.abs(v) / max) * 100}%`, height: "100%", borderRadius: 4, background: v < 0 ? "var(--bad)" : "var(--accent)" }} />
              </div>
              <span className="mono num" style={{ width: 70, textAlign: "right" }}>{Math.abs(v) < 1 ? `${(v * 100).toFixed(1)}%` : v.toFixed(3)}</span>
            </div>
          ))}
        </div>
      </Card>
    );
  }
  return (
    <div className="card">
      <Chart title={p.title} series={p.series.map((s) => ({ name: s.name, x: s.points.map((q) => Number(q[0])), y: s.points.map((q) => q[1]) }))} height={220} />
    </div>
  );
}
