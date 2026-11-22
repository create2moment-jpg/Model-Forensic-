import { useQuery } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import type { UwTimeline } from "../../api/types";
import { Chart, PALETTE } from "../../components/Chart";
import { FindingCard } from "../../components/FindingCard";
import { ParseMessages } from "../../components/ParseMessages";
import { PlotView } from "../../components/PlotView";
import { RunPicker } from "../../components/RunPicker";
import { Card, ErrorBox, Loading, Pill } from "../../components/ui";
import { fmtNum } from "../../lib/format";

const TONE: Record<string, string> = { switched_off: "bad", at_bound: "warn", dominant: "warn", drifting: "info", settled: "good" };
const pct = (x: number | null | undefined, d = 1): string => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(d)} %`);
const step = (x: number | null | undefined): string => (x === null || x === undefined ? "—" : fmtNum(x));

/** Uncertainty-based loss weighting, studied: what the learned σ did to every head over time, how balanced the
 *  tasks stayed, where each σ sits against its optimum, and — against a baseline — which heads gained or lost weight. */
export function UncertaintyTab({ runId }: { runId: string }) {
  const [baseline, setBaseline] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["uncertainty", runId, baseline], queryFn: () => api.uncertainty(runId, baseline) });
  const [only, setOnly] = useState<"all" | "attention">("all");
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (!d.available) return <ParseMessages messages={d.message ? [d.message] : []} />;
  const s = d.summary ?? {};
  const tl = d.timeline && "steps" in d.timeline ? (d.timeline as UwTimeline) : null;
  const bs = tl?.balance_summary;
  const heads = d.heads ?? [];
  const attention = new Set(heads.filter((h) => h.status !== "settled").map((h) => h.head));
  const shown = (h: string): boolean => only === "all" || attention.has(h);
  const lines = (m: Record<string, (number | null)[]>) => Object.entries(m).filter(([h]) => shown(h)).map(([h, ys], i) => ({ name: h, x: tl!.steps, y: ys, color: PALETTE[i % PALETTE.length] }));
  const sync = `uw-${runId}`;
  return (
    <div className="col" style={{ gap: 14 }} data-testid="uncertainty">
      <div className="card row wrap" style={{ gap: 12, padding: "8px 12px" }}>
        <Scale size={15} className="faint" />
        <span className="faint" style={{ fontSize: 12.5 }}>{d.formula}</span>
        <span className="spacer" />
        <span className="faint" style={{ fontSize: 12.5 }}>Study against</span>
        <RunPicker value={baseline} onChange={setBaseline} placeholder="a baseline run…" width={260} />
        {baseline && <button className="btn ghost sm" onClick={() => setBaseline(null)}>clear</button>}
      </div>
      {d.baseline?.error && <div className="note warn">{d.baseline.error}</div>}
      <div className="tiles" data-testid="uw-summary">
        <Tile label="Heads" value={String(s.heads ?? heads.length)} sub={[s.switched_off ? `${s.switched_off} switched off` : "", s.at_bound ? `${s.at_bound} at bound` : "", s.drifting ? `${s.drifting} drifting` : ""].filter(Boolean).join(" · ") || "all settled"} tone={s.switched_off ? "bad" : undefined} />
        {bs && <Tile label="Effective tasks" value={bs.effective_tasks_end.toFixed(1)} sub={`of ${bs.heads} · ${bs.effective_tasks_start.toFixed(1)} at the start`} hint="exp(entropy of the weight shares): how many heads effectively get weight" />}
        {bs && <Tile label="Balance" value={pct(bs.balance_end, 0)} sub={`${pct(bs.balance_start, 0)} at the start`} hint="normalised entropy of the weight shares: 100 % = all heads weighted equally" tone={bs.balance_end < 0.8 ? "bad" : undefined} />}
        {bs && <Tile label="Largest / smallest weight" value={`×${bs.max_min_ratio_end.toFixed(1)}`} sub="at the end of the run" tone={bs.max_min_ratio_end > 5 ? "bad" : undefined} />}
        {d.baseline?.balance && bs && <Tile label="Balance vs baseline" value={`${((bs.balance_end - d.baseline.balance.balance_end) * 100).toFixed(1)} pts`} sub={`baseline ${pct(d.baseline.balance.balance_end, 0)}`} tone={bs.balance_end < d.baseline.balance.balance_end - 0.02 ? "bad" : undefined} />}
      </div>
      {tl && (
        <>
          <div className="row" style={{ gap: 6 }}>
            <span className="faint" style={{ fontSize: 12.5 }}>Heads in the charts</span>
            <button className={`filter-chip ${only === "all" ? "on" : ""}`} onClick={() => setOnly("all")}>all ({heads.length})</button>
            <button className={`filter-chip ${only === "attention" ? "on" : ""}`} onClick={() => setOnly("attention")} disabled={!attention.size}>need attention ({attention.size})</button>
            <span className="faint" style={{ fontSize: 12 }}>· zoom any chart: all follow</span>
          </div>
          <div className="grid2">
            <div className="card"><Chart title="Share of the total weight per head" series={lines(tl.share)} syncKey={sync} height={260} /></div>
            {tl.contribution
              ? <div className="card"><Chart title="Share of the weighted loss per head (weight × own loss)" series={lines(tl.contribution)} syncKey={sync} height={260} /></div>
              : <Card><div className="faint">Each head's own (unweighted) loss is not logged, so its share of the weighted loss cannot be computed.</div></Card>}
            <div className="card"><Chart title="Balance: effective number of tasks" series={[{ name: "effective tasks", x: tl.steps, y: tl.effective_tasks, color: PALETTE[0] }]} hlines={[{ y: bs!.heads, label: "all heads equal" }]} syncKey={sync} height={220} /></div>
            {(d.plots ?? []).filter((p) => p.id === "uw-log-sigma").map((p) => <PlotView key={p.id} spec={p} />)}
          </div>
        </>
      )}
      <Card title="Per head" sub="sortable — click a column" flush>
        <div className="table-wrap">
          <table className="t" data-testid="uw-heads">
            <thead><tr>
              <th>Head</th><th>Status</th><th className="r">share (end)</th><th className="r">share min – max</th><th className="r">log σ start → end</th>
              <th className="r" title="learned log σ minus the optimum ½·log(2L) at the end: > 0 under-weighted, < 0 over-weighted">gap to optimum</th>
              <th className="r" title="correlation of log σ with log of the head's own loss over time (theory: σ follows the loss)">σ ↔ loss</th>
              <th className="r">switched off at</th><th className="r">settled at</th><th className="r">own loss progress</th>
              {d.baseline && !d.baseline.error && <th className="r">Δ share vs baseline</th>}
              <th>Why</th>
            </tr></thead>
            <tbody>{heads.map((h) => (
              <tr key={h.key} data-status={h.status}>
                <td className="mono trunc" style={{ maxWidth: 220 }}>{h.head}</td>
                <td data-sort={["switched_off", "at_bound", "dominant", "drifting", "settled"].indexOf(h.status)}><Pill tone={TONE[h.status] ?? "outline"}>{h.status.replace("_", " ")}</Pill></td>
                <td className="r num" data-sort={h.share}>{pct(h.share)}</td>
                <td className="r num" data-sort={h.share_max ?? ""}>{h.share_min !== undefined ? `${pct(h.share_min)} – ${pct(h.share_max)}` : "—"}</td>
                <td className="r num" data-sort={h.log_sigma.last}>{h.log_sigma.first.toFixed(2)} → {h.log_sigma.last.toFixed(2)}</td>
                <td className="r num" data-sort={h.optimum_gap ?? ""} style={{ color: h.optimum_gap !== null && h.optimum_gap !== undefined && Math.abs(h.optimum_gap) > 0.3 ? "var(--bad)" : undefined }}>{h.optimum_gap === null || h.optimum_gap === undefined ? "—" : `${h.optimum_gap > 0 ? "+" : ""}${h.optimum_gap.toFixed(2)}`}</td>
                <td className="r num" data-sort={h.sigma_loss_corr ?? ""}>{h.sigma_loss_corr === null || h.sigma_loss_corr === undefined ? "—" : h.sigma_loss_corr.toFixed(2)}</td>
                <td className="r num" data-sort={h.switched_off_at ?? ""}>{step(h.switched_off_at)}</td>
                <td className="r num" data-sort={h.settled_at ?? ""}>{step(h.settled_at)}</td>
                <td className="r num" data-sort={h.unweighted_loss_progress ?? ""}>{h.unweighted_loss_progress === null ? "—" : `${(h.unweighted_loss_progress * 100).toFixed(0)} %`}</td>
                {d.baseline && !d.baseline.error && <td className="r num" data-sort={h.share_delta ?? ""} style={{ color: h.share_delta && Math.abs(h.share_delta) > 0.01 ? (h.share_delta < 0 ? "var(--bad)" : "var(--good)") : undefined }}>{h.share_delta === null || h.share_delta === undefined ? "—" : `${h.share_delta > 0 ? "+" : ""}${(h.share_delta * 100).toFixed(1)} pts`}{h.baseline_status && h.baseline_status !== h.status ? <span className="faint"> (was {h.baseline_status.replace("_", " ")})</span> : null}</td>}
                <td className="trunc faint" style={{ maxWidth: 360, fontSize: 12 }}>{h.why}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {d.config && Object.keys(d.config).length > 0 && (
          <div className="faint mono" style={{ fontSize: 12, padding: "8px 12px" }}>{Object.entries(d.config).map(([k, v]) => <div key={k} className="trunc">{k} = {JSON.stringify(v)}</div>)}</div>
        )}
      </Card>
      <div className="grid2">{(d.plots ?? []).filter((p) => p.id !== "uw-log-sigma").map((p) => <PlotView key={p.id} spec={p} />)}</div>
      {(d.findings ?? []).map((f) => <FindingCard key={f.id} f={f} />)}
    </div>
  );
}

function Tile({ label, value, sub, hint, tone }: { label: string; value: string; sub?: string; hint?: string; tone?: "bad" }) {
  return (
    <div className="card metric-tile" style={{ height: 96 }} title={hint}>
      <div className="mt-name trunc">{label}</div>
      <div className="mt-value" style={{ color: tone === "bad" ? "var(--bad)" : undefined }}>{value}</div>
      {sub && <div className="mt-detail trunc">{sub}</div>}
    </div>
  );
}
