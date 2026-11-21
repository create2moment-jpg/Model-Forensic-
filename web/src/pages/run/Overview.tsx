import { ArrowDownRight, ArrowUpRight, GitBranch } from "lucide-react";
import { Link } from "react-router-dom";
import { refStr } from "../../api/client";
import type { MetricDelta, RunOverview } from "../../api/types";
import { FindingCard, sortFindings } from "../../components/FindingCard";
import { RunPicker } from "../../components/RunPicker";
import { Card, DeltaBar, Empty, Pill } from "../../components/ui";
import { fmtDate, fmtNum, fmtPct, fmtValue } from "../../lib/format";

const worse = (d: MetricDelta): boolean | null => (d.higher_is_better === null || d.relative === null ? null : d.higher_is_better ? d.relative < 0 : d.relative > 0);

export function OverviewTab({ o, runId, baseline, setBaseline, goTab }: { o: RunOverview; runId: string; baseline: string | null; setBaseline: (b: string) => void; goTab: (t: "findings" | "config" | "metrics") => void }) {
  const r = o.run;
  const wc = o.what_changed;
  const deltaBy = Object.fromEntries((wc?.deltas ?? []).map((d) => [d.metric, d]));
  const headline = Object.entries(o.summary).filter(([k]) => !k.includes("[")).sort(([a], [b]) => a.localeCompare(b));
  const changes = (wc?.config_diff.changes ?? []).filter((c) => !c.is_noise);
  const moved = (wc?.deltas ?? []).filter((d) => d.verdict === "regressed" || d.verdict === "improved").sort((a, b) => Math.abs(b.relative ?? 0) - Math.abs(a.relative ?? 0));

  return (
    <div className="col" style={{ gap: 14 }}>
      {headline.length > 0 && (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
          {headline.map(([k, v]) => {
            const d = deltaBy[k];
            const w = d ? worse(d) : null;
            return (
              <div key={k} className="card stat" style={{ padding: "12px 14px" }}>
                <div className="label mono">{k}</div>
                <div className="value" style={{ fontSize: 20 }}>{fmtNum(v)}</div>
                {d && d.verdict !== "unknown" && (
                  <div className="delta row" style={{ gap: 3, color: d.verdict === "unchanged" ? "var(--text-3)" : w ? "var(--bad)" : "var(--good)" }}>
                    {(d.relative ?? 0) >= 0 ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{fmtPct(d.relative)}
                    <span className="faint">{d.verdict === "unchanged" ? "within noise" : d.z_score !== null ? `z ${d.z_score.toFixed(1)}` : ""}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="split">
        <Card title="What changed?" sub={baseline ? `vs ${baseline}` : "pick a baseline"} right={<RunPicker value={baseline} onChange={setBaseline} placeholder="Baseline…" width={240} />}>
          {!wc ? <Empty title="No baseline selected">Pick a baseline run to see configuration changes and metric deltas. Runs tagged <code>role=baseline</code> in the same experiment are picked automatically.</Empty> : (
            <div className="col" style={{ gap: 16 }}>
              <div className="row wrap" style={{ gap: 6 }}>
                <Pill tone={wc.baseline_replicates.length >= 2 ? "good" : "warn"}>
                  {wc.baseline_replicates.length >= 2 ? `significance vs ${wc.baseline_replicates.length + 1} baseline seeds` : "no replicates: threshold-based significance"}
                </Pill>
                <Pill tone="outline">{changes.length} config changes</Pill>
                <Pill tone="bad">{wc.deltas.filter((d) => d.verdict === "regressed").length} regressed</Pill>
                <Pill tone="good">{wc.deltas.filter((d) => d.verdict === "improved").length} improved</Pill>
                {wc.duration_ratio && <Pill tone="outline">duration ×{wc.duration_ratio.toFixed(2)}</Pill>}
              </div>
              <div>
                <div className="sub-h">Configuration</div>
                {changes.length === 0 ? <span className="faint">Identical configuration (excluding runtime paths/names).</span> : (
                  <table className="t compact">
                    <tbody>
                      {changes.map((c) => (
                        <tr key={c.path}>
                          <td className="mono wrap">{c.path}</td>
                          <td className="mono faint" style={{ textDecoration: c.kind !== "added" ? "line-through" : undefined }}>{c.kind === "added" ? "—" : fmtValue(c.baseline)}</td>
                          <td className="mono">{c.kind === "removed" ? "—" : fmtValue(c.candidate)}</td>
                          <td>{c.relative_change !== null && <span className="faint">{fmtPct(c.relative_change, 0)}</span>}</td>
                          <td><span className="tag">{c.category}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div>
                <div className="sub-h">Metrics that moved beyond noise</div>
                {moved.length === 0 ? <span className="faint">No significant metric changes.</span> : (
                  <table className="t compact">
                    <tbody>
                      {moved.slice(0, 14).map((d) => (
                        <tr key={d.metric}>
                          <td className="mono">{d.metric}</td>
                          <td className="r num faint">{fmtNum(d.baseline)}</td>
                          <td className="r num">{fmtNum(d.candidate)}</td>
                          <td className="r num" style={{ color: worse(d) ? "var(--bad)" : "var(--good)" }}>{fmtPct(d.relative)}</td>
                          <td style={{ width: 140 }}><DeltaBar rel={d.relative} worse={worse(d)} /></td>
                          <td className="r faint num">{d.z_score !== null ? `z ${d.z_score.toFixed(1)}` : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div className="row">
                <button className="btn sm" onClick={() => goTab("findings")}>Run forensic analyses →</button>
                <Link className="btn sm ghost" to={`/compare?baseline=${encodeURIComponent(baseline ?? "")}&candidate=${encodeURIComponent(runId)}`}>Full comparison →</Link>
              </div>
            </div>
          )}
        </Card>

        <div className="col" style={{ gap: 14 }}>
          <Card title="Details">
            <dl className="kv">
              <dt>Experiment</dt><dd>{r.experiment ?? "—"}</dd>
              <dt>Created</dt><dd>{fmtDate(r.created_at)}</dd>
              <dt>Git</dt><dd className="mono row" style={{ gap: 4 }}><GitBranch size={12} />{r.git.branch ?? "—"} @ {r.git.commit ?? "—"}{r.git.dirty ? " (dirty)" : ""}</dd>
              <dt>Compute</dt><dd>{r.compute.target ?? "—"} {r.compute.instance_type && <span className="faint">· {r.compute.instance_type}</span>}</dd>
              <dt>GPUs</dt><dd>{r.compute.gpu_count ? `${r.compute.node_count ?? 1} × ${r.compute.gpu_count} × ${r.compute.gpu_type ?? "GPU"}` : "—"}</dd>
              <dt>Environment</dt><dd className="mono">{r.environment ?? "—"}</dd>
              <dt>Command</dt><dd className="mono">{r.command ?? "—"}</dd>
              <dt>Datasets</dt><dd>{r.datasets.map((d) => `${d.name}${d.version ? `:${d.version}` : ""}`).join(", ") || "—"}</dd>
              {r.parent && <><dt>Parent</dt><dd><Link className="mono" style={{ color: "var(--accent-2)" }} to={`/runs/${encodeURIComponent(refStr(r.parent))}`}>{r.parent.run_id}</Link></dd></>}
              {r.children.length > 0 && <><dt>Children</dt><dd className="col" style={{ gap: 2 }}>{r.children.map((c) => <Link key={c.run_id} className="mono" style={{ color: "var(--accent-2)" }} to={`/runs/${encodeURIComponent(refStr(c))}`}>{c.run_id}</Link>)}</dd></>}
              {r.description && <><dt>Description</dt><dd>{r.description}</dd></>}
            </dl>
            {Object.keys(r.tags).length > 0 && <div className="row wrap" style={{ gap: 4, marginTop: 12 }}>{Object.entries(r.tags).map(([k, v]) => <span key={k} className="tag">{k}={v}</span>)}</div>}
          </Card>
          <Card title="Stored findings" sub={`${o.findings.length}`} right={<button className="btn ghost sm" onClick={() => goTab("findings")}>Open</button>}>
            {o.findings.length === 0 ? <span className="faint">No analyses run yet for this run.</span> : sortFindings(o.findings).slice(0, 4).map((f) => <FindingCard key={f.id} f={f} />)}
          </Card>
        </div>
      </div>
    </div>
  );
}
