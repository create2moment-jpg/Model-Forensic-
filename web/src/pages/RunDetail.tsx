import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { RunLoadingPanel } from "../components/RunLoading";
import type { RunOverview, TaskView } from "../api/types";
import { BookOpenCheck, GanttChart, ChartSpline, Scale, Wrench, Loader2, Bot, Copy, Cpu, Database, Target, Download, ExternalLink, FileCode2, FileText, GitCompare, LayoutDashboard, LineChart, Microscope, Package, Play, Rows3, ScrollText, ShieldAlert } from "lucide-react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { planAndOpen } from "./Prefetch";
import { TimelineTab } from "./run/Timeline";
import { ExpertReview } from "./run/ExpertReview";
import { DossierButton } from "../components/DossierButton";
import { PlotsTab } from "./run/Plots";
import { UncertaintyTab } from "./run/Uncertainty";
import { SetupCluesTab } from "./run/SetupClues";
import { AddToMe } from "../components/MeSettings";
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
import { useFeatures } from "../lib/features";

type Tab = "overview" | "review" | "timeline" | "metrics" | "plots" | "uncertainty" | "setup" | "findings" | "precision" | "sequences" | "config" | "dataset" | "compute" | "logs" | "artifacts" | "ai";

export function RunDetailPage() {
  const runId = decodeURIComponent(useParams().runId ?? "");
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) ?? "overview";
  const feats = useFeatures();  // Settings → Features: tabs the level (or the user) switched off are hidden
  const baselineParam = sp.get("baseline");
  const nav = useNavigate();
  const { toast } = useApp();
  const qc = useQueryClient();
  // Every run opens in Light Mode; Full is a choice for this run view only (never remembered for the next run).
  const [mode, setMode] = useState<"light" | "full">("light");
  useEffect(() => setMode("light"), [runId]);
  // 1. the shell at once (index + caches only)
  const light = useQuery({ queryKey: ["run-light", runId], queryFn: () => api.runLight(runId) });
  // 2. the run's parts as application-scoped tasks: Light/Full Mode, plus what the open tab needs
  const kinds = TAB_KINDS[tab] ?? [];
  useEffect(() => {
    void api.runLoad(runId, mode, kinds).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] })).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, mode, kinds.join(",")]);
  const tasksQ = useQuery({
    queryKey: ["run-tasks", runId], queryFn: () => api.runTasks(runId),
    refetchInterval: (q) => ((q.state.data?.tasks ?? []).some((t) => t.active) ? 500 : 15_000),
  });
  const tasks = tasksQ.data?.tasks ?? [];
  const doneKey = useMemo(() => tasks.filter((t) => !t.active).map((t) => t.id).sort().join(","), [tasks]);
  // 3. the overview from what is loaded so far — refreshed each time a task finishes
  // Headline trends: only once the histories are loaded (never a reason to load them by itself)
  const trendKeys = (light.data?.headline ?? []).join(",");
  const seriesReady = !!(light.data?.cached.series) || tasks.some((t) => t.kind === "run.metrics" && t.state === "done");
  const trends = useQuery({
    queryKey: ["trends", runId, trendKeys], enabled: seriesReady && !!trendKeys,
    queryFn: async () => Object.fromEntries((await api.metrics(runId, light.data!.headline!, 80)).map((s) => [s.key, s.values])) as Record<string, (number | null)[]>,
  });
  const ov = useQuery({ queryKey: ["overview-cached", runId, baselineParam, doneKey], queryFn: () => api.overviewCached(runId, baselineParam), placeholderData: (prev) => prev });
  const set = (patch: Record<string, string | null>): void => {
    const n = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v === null ? n.delete(k) : n.set(k, v)));
    setSp(n, { replace: true });
  };

  if (light.error) return <div className="page"><ErrorBox error={light.error} /></div>;
  if (!light.data) return <div className="page"><Loading label="Finding the run…" /></div>;
  const r = light.data.run;
  const o: RunOverview = ov.data ?? { run: r, metric_keys: [], summary: {}, configs: [], artifacts: [], baseline: null, what_changed: null, findings: [], ready: { series: false, configs: false, artifacts: false, comparison: false } };
  const ready = o.ready ?? { series: true, configs: true, artifacts: true, comparison: true };
  void trendKeys;
  const taskFor = (k: string) => tasks.find((t) => t.kind === k);
  const pending = (k: string, what: string) => <PendingPart task={taskFor(k)} what={what} />;
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
            <span>·</span><span>{r.user ?? "unknown user"}</span><AddToMe user={r.user} />
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
          <DossierButton run={runId} baseline={baseline} />
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
      <RunLoadingPanel tasks={tasks} mode={mode} onMode={setMode}
        onLoadMetrics={() => void api.runLoad(runId, mode, ["run.metrics"]).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] }))} />
      <Tabs<Tab>
        value={tab}
        onChange={(t) => set({ tab: t, analyze: null })}
        tabs={([
          { id: "overview", label: "Overview", icon: <LayoutDashboard size={14} /> },
          { id: "review", label: "Expert review", icon: <BookOpenCheck size={14} /> },
          { id: "timeline", label: "Timeline", icon: <GanttChart size={14} /> },
          { id: "metrics", label: "Metrics", icon: <LineChart size={14} />, count: ready.series ? o.metric_keys.length : undefined },
          { id: "plots", label: "Plots", icon: <ChartSpline size={14} /> },
          { id: "uncertainty", label: "Uncertainty", icon: <Scale size={14} /> },
          { id: "setup", label: "Setup", icon: <Wrench size={14} /> },
          { id: "findings", label: "Findings", icon: <ShieldAlert size={14} />, count: o.findings.length },
          { id: "precision", label: "Precision & recall", icon: <Target size={14} /> },
          ...(o.artifacts.some((a) => a.path.toLowerCase().endsWith(".parquet"))
            ? [{ id: "sequences" as const, label: "Sequences", icon: <Rows3 size={14} />, count: o.artifacts.filter((a) => a.path.toLowerCase().endsWith(".parquet")).length }]
            : []),
          { id: "config", label: "Config", icon: <FileCode2 size={14} /> },
          { id: "dataset", label: "Dataset", icon: <Database size={14} /> },
          { id: "compute", label: "Compute", icon: <Cpu size={14} /> },
          { id: "logs", label: "Logs", icon: <ScrollText size={14} /> },
          { id: "artifacts", label: "Artifacts", icon: <Package size={14} />, count: ready.artifacts ? o.artifacts.length : undefined },
          { id: "ai", label: "AI", icon: <Bot size={14} /> },
        ] as { id: Tab; label: string; icon: ReactNode; count?: number }[]).filter((t) => t.id === tab || feats.on(`run.${t.id}`))}
      />
      {tab === "overview" && <OverviewTab o={ready.series ? o : { ...o, summary: light.data.summary_metrics }} headlineKeys={light.data.headline ?? []}
        trends={trends.data} onShowTrends={() => void api.runLoad(runId, mode, ["run.metrics"]).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] }))} runId={runId} baseline={baseline} setBaseline={(b) => set({ baseline: b })} goTab={(t) => set({ tab: t })}
        full={mode === "full"} onLoadComparison={() => void api.runLoad(runId, mode, ["run.comparison"]).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] }))} />}
      {tab === "review" && (ready.series ? <ExpertReview runId={runId} baseline={baseline} onPlot={(ks) => set({ tab: "metrics", mk: ks.join("|") })} /> : pending("run.metrics", "metric histories"))}
      {tab === "timeline" && <TimelineTab runId={runId} />}
      {tab === "plots" && (ready.series ? <PlotsTab runId={runId} /> : pending("run.metrics", "metric histories"))}
      {tab === "uncertainty" && (ready.series ? <UncertaintyTab runId={runId} /> : pending("run.metrics", "metric histories"))}
      {tab === "setup" && <SetupCluesTab runId={runId} />}
      {tab === "metrics" && (ready.series ? <MetricsExplorer key={sp.get("mk") ?? ""} runId={runId} keys={o.metric_keys} baseline={baseline} initialPicked={sp.get("mk")?.split("|")} /> : pending("run.metrics", "metric histories"))}
      {tab === "findings" && <FindingsTab runId={runId} baseline={baseline} autoRun={sp.get("analyze") === "1"} stored={o.findings} />}
      {tab === "config" && <ConfigTab runId={runId} baseline={baseline} />}
      {tab === "precision" && <PrecisionRecallTab runId={runId} />}
      {tab === "dataset" && <DatasetTab runId={runId} />}
      {tab === "compute" && <ComputeTab runId={runId} />}
      {tab === "logs" && <LogsTab runId={runId} />}
      {tab === "sequences" && <SequencesTab runId={runId} />}
      {tab === "artifacts" && (ready.artifacts ? <ArtifactsTab runId={runId} artifacts={o.artifacts} /> : pending("run.artifacts", "file listing"))}
      {tab === "ai" && <InvestigationPanel run={runId} baseline={baseline} />}
    </div>
  );
}

/** What each tab needs loaded (Light Mode loads the rest only when asked). */
const TAB_KINDS: Partial<Record<Tab, string[]>> = {
  overview: [], metrics: ["run.metrics"], findings: ["run.metrics", "run.configs"], artifacts: ["run.artifacts"], sequences: ["run.artifacts"],
  config: ["run.configs"], review: ["run.metrics", "run.configs"], plots: ["run.metrics"], uncertainty: ["run.metrics", "run.configs"], setup: ["run.configs", "run.metrics"], timeline: ["run.stages", "run.artifacts"],
};

/** A part that is not loaded yet: say which task fills it, what it is doing and for how long — never an empty view. */
function PendingPart({ task, what }: { task: TaskView | undefined; what: string }) {
  if (task?.state === "failed") return <div className="card card-body"><div className="note warn">Loading the {what} failed: {task.error}</div></div>;
  return (
    <div className="card card-body row" data-testid="pending-part" style={{ gap: 10 }}>
      <Loader2 size={16} className="spin" color="var(--accent)" />
      <div>
        <div>Loading the {what}{task ? ` — ${task.state === "waiting" ? "waiting for its inputs" : task.state}${task.started_at ? `, ${task.elapsed_s.toFixed(1)} s` : ""}` : " — starting"}</div>
        {task?.why && <div className="faint" style={{ fontSize: 12 }}>{task.cost.replace("_", " ")}: {task.why}</div>}
      </div>
    </div>
  );
}
