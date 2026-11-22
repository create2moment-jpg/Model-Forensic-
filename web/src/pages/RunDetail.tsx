import { useQuery } from "@tanstack/react-query";
import { Bot, Copy, Cpu, Database, Target, Download, ExternalLink, FileCode2, FileText, GitCompare, LayoutDashboard, LineChart, Microscope, Package, Play, Rows3, ScrollText, ShieldAlert } from "lucide-react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { planAndOpen } from "./Prefetch";
import { api } from "../api/client";
import { useApp } from "../components/AppState";
import { InvestigationPanel } from "../components/Investigation";
import { ErrorBox, Loading, StatusPill, Tabs } from "../components/ui";
import { fmtAgo, fmtDuration } from "../lib/format";
import { ArtifactsTab } from "./run/Artifacts";
import { SequencesTab } from "./run/Sequences";
import { ConfigTab } from "./run/Config";
import { FindingsTab } from "./run/Findings";
import { LogsTab } from "./run/Logs";
import { MetricsExplorer } from "./run/Metrics";
import { OverviewTab } from "./run/Overview";
import { ComputeTab, DatasetTab } from "./run/RunInfo";
import { PrecisionRecallTab } from "./run/PrecisionRecall";

type Tab = "overview" | "metrics" | "findings" | "precision" | "sequences" | "config" | "dataset" | "compute" | "logs" | "artifacts" | "ai";

export function RunDetailPage() {
  const runId = decodeURIComponent(useParams().runId ?? "");
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) ?? "overview";
  const baselineParam = sp.get("baseline");
  const nav = useNavigate();
  const { toast } = useApp();
  const ov = useQuery({ queryKey: ["overview", runId, baselineParam], queryFn: () => api.overview(runId, baselineParam) });
  const set = (patch: Record<string, string | null>): void => {
    const n = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v === null ? n.delete(k) : n.set(k, v)));
    setSp(n, { replace: true });
  };

  if (ov.isLoading) return <div className="page"><Loading label="Loading run…" /></div>;
  if (ov.error || !ov.data) return <div className="page"><ErrorBox error={ov.error} /></div>;
  const o = ov.data;
  const r = o.run;
  const baseline = baselineParam ?? o.baseline;
  const studio = r.backend_extras?.studio_url;

  return (
    <div className="page">
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <div className="row wrap" style={{ gap: 10 }}>
            <h1 className="ellipsis">{r.display_name ?? r.name}</h1>
            <StatusPill status={r.status} />
            <span className="tag">{r.kind}</span>
          </div>
          <div className="sub row wrap" style={{ gap: 10 }}>
            <span className="mono">{runId}</span>
            <button className="btn ghost sm icon" title="Copy id" onClick={() => { void navigator.clipboard.writeText(runId); toast("Copied run id"); }}><Copy size={12} /></button>
            <span>·</span><span>{r.experiment ?? "no experiment"}</span>
            <span>·</span><span>{r.user ?? "unknown user"}</span>
            <span>·</span><span>{fmtAgo(r.created_at)}</span>
            <span>·</span><span>{fmtDuration(r.started_at, r.ended_at)}</span>
            {studio && <a className="row" style={{ color: "var(--accent-2)" }} href={studio} target="_blank" rel="noreferrer">Azure ML Studio <ExternalLink size={12} /></a>}
          </div>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => set({ tab: "findings", analyze: "1" })}><Play size={14} /> Analyze</button>
          <button className="btn" onClick={() => nav(`/compare?candidate=${encodeURIComponent(runId)}${baseline ? `&baseline=${encodeURIComponent(baseline)}` : ""}`)}><GitCompare size={14} /> Compare</button>
          <button className="btn" title="Plan a fast-debug prefetch: review files and sizes, then start it" onClick={() => void planAndOpen(nav, { run: runId, profile: "fast_debug" }).catch((e: unknown) => toast(String(e), "bad"))}><Download size={14} /> Bundle</button>
          <button className="btn" onClick={() => nav(`/reports?run=${encodeURIComponent(runId)}${baseline ? `&baseline=${encodeURIComponent(baseline)}` : ""}`)}><FileText size={14} /> Report</button>
          <button className="btn" onClick={() => set({ tab: "ai" })}><Bot size={14} /> Ask AI</button>
          <button className="btn primary" onClick={() => nav(`/studies?runs=${encodeURIComponent(runId)}${baseline ? `&baseline=${encodeURIComponent(baseline)}` : ""}`)}><Microscope size={14} /> Study</button>
        </div>
      </div>
      {r.failure && (
        <div className="error-box" style={{ marginBottom: 14 }}>
          <ShieldAlert size={18} color="var(--bad)" style={{ flex: "none" }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>Failed{r.failure.category ? ` · ${r.failure.category}` : ""}</div>
            <div className="mono muted" style={{ whiteSpace: "pre-wrap" }}>{r.failure.message}</div>
          </div>
          <button className="btn sm" style={{ marginLeft: "auto" }} onClick={() => set({ tab: "logs" })}>Open logs</button>
        </div>
      )}
      <Tabs<Tab>
        value={tab}
        onChange={(t) => set({ tab: t, analyze: null })}
        tabs={[
          { id: "overview", label: "Overview", icon: <LayoutDashboard size={14} /> },
          { id: "metrics", label: "Metrics", icon: <LineChart size={14} />, count: o.metric_keys.length },
          { id: "findings", label: "Findings", icon: <ShieldAlert size={14} />, count: o.findings.length },
          { id: "precision", label: "Precision & recall", icon: <Target size={14} /> },
          ...(o.artifacts.some((a) => a.path.toLowerCase().endsWith(".parquet"))
            ? [{ id: "sequences" as const, label: "Sequences", icon: <Rows3 size={14} />, count: o.artifacts.filter((a) => a.path.toLowerCase().endsWith(".parquet")).length }]
            : []),
          { id: "config", label: "Config", icon: <FileCode2 size={14} /> },
          { id: "dataset", label: "Dataset", icon: <Database size={14} /> },
          { id: "compute", label: "Compute", icon: <Cpu size={14} /> },
          { id: "logs", label: "Logs", icon: <ScrollText size={14} /> },
          { id: "artifacts", label: "Artifacts", icon: <Package size={14} />, count: o.artifacts.length },
          { id: "ai", label: "AI", icon: <Bot size={14} /> },
        ]}
      />
      {tab === "overview" && <OverviewTab o={o} runId={runId} baseline={baseline} setBaseline={(b) => set({ baseline: b })} goTab={(t) => set({ tab: t })} />}
      {tab === "metrics" && <MetricsExplorer runId={runId} keys={o.metric_keys} baseline={baseline} />}
      {tab === "findings" && <FindingsTab runId={runId} baseline={baseline} autoRun={sp.get("analyze") === "1"} stored={o.findings} />}
      {tab === "config" && <ConfigTab runId={runId} baseline={baseline} />}
      {tab === "precision" && <PrecisionRecallTab runId={runId} />}
      {tab === "dataset" && <DatasetTab runId={runId} />}
      {tab === "compute" && <ComputeTab runId={runId} />}
      {tab === "logs" && <LogsTab runId={runId} />}
      {tab === "sequences" && <SequencesTab runId={runId} />}
      {tab === "artifacts" && <ArtifactsTab runId={runId} artifacts={o.artifacts} />}
      {tab === "ai" && <InvestigationPanel run={runId} baseline={baseline} />}
    </div>
  );
}
