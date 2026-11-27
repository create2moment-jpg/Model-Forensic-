import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { RunLoadingPanel } from "../components/RunLoading";
import type { RunOverview, TaskView } from "../api/types";
import { Activity, BookOpenCheck, Eye, EyeOff, GanttChart, ChartSpline, Scale, Wrench, Loader2, Bot, Copy, Cpu, Database, Target, Download, ExternalLink, FileCode2, FileHeart, FileText, FolderTree, GitCompare, LayoutDashboard, Link2, LineChart, Microscope, Package, Play, Rows3, ScrollText, ShieldAlert, Terminal, X, Zap } from "lucide-react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { planAndOpen } from "../lib/prefetch";
import { TimelineTab } from "./run/Timeline";
import { ExpertReview } from "./run/ExpertReview";
import { StageSelect, useStageRun } from "../components/StageSelect";
import { LiveRefresh } from "../components/Live";
import { useDossier } from "../components/DossierButton";
import { VerdictCard } from "../components/Verdict";
import { PlotsTab } from "./run/Plots";
import { UncertaintyTab } from "./run/Uncertainty";
import { SetupCluesTab } from "./run/SetupClues";
import { AddToMe } from "../components/MeSettings";
import { api } from "../api/client";
import { useApp } from "../components/AppState";
import { InvestigationPanel } from "../components/Investigation";
import { ErrorBox, Loading, Menu, ProgressLine, StatusPill, Tabs } from "../components/ui";
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
import { useHotkey } from "../lib/hooks";
import { useWatched } from "../lib/watch";
import { lastStage, remember, rememberStage } from "../lib/recent";

type Tab = "overview" | "review" | "timeline" | "metrics" | "plots" | "uncertainty" | "setup" | "findings" | "precision" | "sequences" | "config" | "dataset" | "compute" | "logs" | "artifacts" | "ai";
type Group = "summary" | "metrics" | "review" | "files" | "timeline";

/** Five places on the run page; a place with several views shows them as a quiet second row. ``?tab=`` keeps naming
 *  the view, so every link from before still opens the same thing. */
const GROUPS: { id: Group; label: string; icon: ReactNode; views: Tab[] }[] = [
  { id: "summary", label: "Summary", icon: <LayoutDashboard size={14} />, views: ["overview"] },
  { id: "metrics", label: "Metrics", icon: <LineChart size={14} />, views: ["metrics", "plots", "uncertainty"] },
  { id: "review", label: "Review", icon: <BookOpenCheck size={14} />, views: ["review", "findings", "precision"] },
  { id: "files", label: "Files", icon: <FolderTree size={14} />, views: ["config", "logs", "artifacts", "dataset", "compute", "setup", "sequences"] },
  { id: "timeline", label: "Timeline", icon: <GanttChart size={14} />, views: ["timeline"] },
];
const VIEW_LABEL: Partial<Record<Tab, [string, ReactNode]>> = {
  metrics: ["Explorer", <LineChart size={13} />], plots: ["Plots", <ChartSpline size={13} />], uncertainty: ["Uncertainty", <Scale size={13} />],
  review: ["Expert review", <BookOpenCheck size={13} />], findings: ["Findings", <ShieldAlert size={13} />], precision: ["Precision & recall", <Target size={13} />],
  config: ["Config", <FileCode2 size={13} />], logs: ["Logs", <ScrollText size={13} />], artifacts: ["Artifacts", <Package size={13} />],
  dataset: ["Dataset", <Database size={13} />], compute: ["Compute", <Cpu size={13} />], setup: ["Setup", <Wrench size={13} />], sequences: ["Sequences", <Rows3 size={13} />],
};
const groupOf = (t: Tab): Group => GROUPS.find((g) => g.views.includes(t))?.id ?? "summary";

export function RunDetailPage() {
  const runId = decodeURIComponent(useParams().runId ?? "");
  const [sp, setSp] = useSearchParams();
  const asked = (sp.get("tab") as Tab) ?? "overview";
  const tab: Tab = asked === "ai" ? "overview" : asked;  // the AI view opens as the Ask drawer
  const askOpen = asked === "ai" || sp.get("ask") === "1";
  const feats = useFeatures();  // Settings → Features: tabs the level (or the user) switched off are hidden
  const baselineParam = sp.get("baseline");
  const nav = useNavigate();
  const { toast } = useApp();
  const qc = useQueryClient();
  // Every run opens in Light Mode; Full is a choice for this run view only (never remembered for the next run).
  const [mode, setMode] = useState<"light" | "full">("light");
  const [showLoading, setShowLoading] = useState(false);
  useEffect(() => setMode("light"), [runId]);
  // 1. the shell at once (index + caches only)
  const light = useQuery({ queryKey: ["run-light", runId], queryFn: () => api.runLight(runId) });
  // the stage every stage-aware view reads (?stage=…): pretrain, finetune and evaluation are never mixed
  const stage = sp.get("stage") ?? "";
  // the pipeline's stages (a cheap listing — never waits for the metric histories)
  const stageList = useQuery({ queryKey: ["stages", runId], queryFn: () => api.stages(runId), enabled: !!runId && !runId.startsWith("group:"), staleTime: 60_000 });
  const listed = stageList.data?.map((s) => s.name);
  // 2. the run's parts as application-scoped tasks: Light/Full Mode, plus what the open tab needs. With a stage, the
  //    stage-aware tabs read that stage alone (fetched on its own): the other stages' histories are not waited for
  //    — "Load all stages" (StageScope) fetches them when wanted
  const stageAlone = !!stage && (!listed || listed.includes(stage)) && STAGE_ALONE.has(tab);
  const kinds = (TAB_KINDS[tab] ?? []).filter((k) => !(stageAlone && k === "run.metrics"));
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
  // the verdict: from what is loaded (never a reason to fetch the histories by itself); refreshed as parts arrive
  const stageOk = !stage || !listed || listed.includes(stage);
  const verdictQ = useQuery({
    queryKey: ["verdict", runId, baselineParam, stageOk ? stage : "", doneKey, seriesReady],
    queryFn: () => api.verdict(runId, baselineParam, stageOk ? stage : null, !seriesReady && !(stage && stageOk)),
    placeholderData: (prev) => prev, retry: false,
  });
  const autoBase = baselineParam ?? verdictQ.data?.baseline?.ref ?? null;  // picked for you, with the reason
  // a local run is cheap to read: its verdict and the comparison come without asking; a remote one waits for a click
  const v0 = verdictQ.data;
  useEffect(() => {
    if (v0 && v0.state === "not_loaded" && !v0.remote) {
      void api.runLoad(runId, "light", ["run.metrics", ...(autoBase ? ["run.comparison"] : [])], autoBase).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] })).catch(() => undefined);
    }
  }, [runId, v0?.state, v0?.remote, autoBase]);  // eslint-disable-line react-hooks/exhaustive-deps
  const ov = useQuery({ queryKey: ["overview-cached", runId, autoBase, doneKey], queryFn: () => api.overviewCached(runId, autoBase), placeholderData: (prev) => prev });
  const stageCat = useQuery({ queryKey: ["metric-catalog", runId], queryFn: () => api.metricCatalog(runId), staleTime: 60_000, enabled: seriesReady && !listed?.length });
  const runStages = listed?.length ? listed : stageCat.data?.stages ?? [];
  const stageRun = useStageRun(runId, stage);  // a stage is a run of its own: config, logs, findings, setup read it
  const baseStageRun = useStageRun(baselineParam ?? "", stage);
  const dossier = useDossier(runId, baselineParam, stage || null);
  const [watched, setWatched] = useWatched();
  useHotkey(".", () => document.querySelector<HTMLButtonElement>("[data-testid=more-actions]")?.click());
  const set = (patch: Record<string, string | null>): void => {
    if ("stage" in patch && runId) rememberStage(runId, patch.stage ?? "");  // the pipeline opens on it next time
    const n = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v === null ? n.delete(k) : n.set(k, v)));
    setSp(n, { replace: true });
  };

  // a pipeline opens on the stage you last picked on it (unless the link names one)
  const restored = useRef("");
  useEffect(() => {
    if (!runId || restored.current === runId || !listed) return;
    restored.current = runId;
    const last = lastStage(runId);
    if (!sp.get("stage") && last && listed.includes(last)) {
      const n = new URLSearchParams(sp);
      n.set("stage", last);
      setSp(n, { replace: true });
    }
  }, [runId, listed, sp, setSp]);
  // recently opened (⌘K, Today): this run, in the view it is in now
  const title = light.data ? light.data.run.display_name ?? light.data.run.name : "";
  const viewQuery = sp.toString();
  useEffect(() => {
    if (!runId || !title) return;
    const run = light.data?.run;
    remember({ kind: "run", key: runId, title, sub: [run?.experiment, run?.status].filter(Boolean).join(" · "), href: `/runs/${encodeURIComponent(runId)}${viewQuery ? `?${viewQuery}` : ""}` });
  }, [runId, title, viewQuery]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (light.error) return <div className="page"><ErrorBox error={light.error} /></div>;
  if (!light.data) return <div className="page"><Loading label="Finding the run…" /></div>;
  const r = light.data.run;
  const o: RunOverview = ov.data ?? { run: r, metric_keys: [], summary: {}, configs: [], artifacts: [], baseline: null, what_changed: null, findings: [], ready: { series: false, configs: false, artifacts: false, comparison: false } };
  const ready = o.ready ?? { series: true, configs: true, artifacts: true, comparison: true };
  void trendKeys;
  const taskFor = (k: string) => tasks.find((t) => t.kind === k);
  const pending = (k: string, what: string) => <PendingPart task={taskFor(k)} what={what} />;
  // a stage of a pipeline whose histories are not all loaded: that stage's views read it alone
  const oneStage = !!stage && runStages.includes(stage) && !ready.series;
  const series = ready.series || oneStage;
  const loadAll = (): void => void api.runLoad(runId, mode, ["run.metrics"]).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] }));
  const baseline = baselineParam ?? o.baseline ?? autoBase;
  const studio = r.backend_extras?.studio_url;
  const group = groupOf(tab);
  const live = r.status === "running" || r.status === "queued";
  const watching = watched.some((w) => w.ref === runId);
  const lastView = (g: Group): Tab => {
    const views = GROUPS.find((x) => x.id === g)!.views.filter((v) => feats.on(`run.${v}`) || v === tab);
    try { const v = localStorage.getItem(`mf.run.view.${g}`) as Tab | null; if (v && views.includes(v)) return v; } catch { /* storage blocked */ }
    return views[0] ?? GROUPS.find((x) => x.id === g)!.views[0]!;
  };
  const goView = (v: Tab): void => { try { localStorage.setItem(`mf.run.view.${groupOf(v)}`, v); } catch { /* storage blocked */ } set({ tab: v, analyze: null }); };
  const hasParquet = o.artifacts.some((a) => a.path.toLowerCase().endsWith(".parquet"));
  const viewsOf = (g: Group): Tab[] => GROUPS.find((x) => x.id === g)!.views
    .filter((v) => v !== "sequences" || hasParquet || tab === "sequences")
    .filter((v) => v === tab || feats.on(`run.${v}`));
  const counts: Partial<Record<Tab, number | undefined>> = {
    metrics: ready.series ? o.metric_keys.length : undefined, findings: o.findings.length || undefined,
    artifacts: ready.artifacts ? o.artifacts.length : undefined, sequences: o.artifacts.filter((a) => a.path.toLowerCase().endsWith(".parquet")).length || undefined,
  };
  const active = tasks.filter((t) => t.active);
  const q = (b: string | null): string => (b ? `&baseline=${encodeURIComponent(b)}` : "");
  const cli = `mf ${tab === "review" ? "dossier" : "verdict"} --run ${runId}${baselineParam ? ` --baseline ${baselineParam}` : ""}${stage ? ` --stage ${stage}` : ""}`;
  const menu = [
    { label: "Analyze (findings)", icon: <Play size={14} />, onSelect: () => set({ tab: "findings", analyze: "1" }) },
    { label: "Report", icon: <FileText size={14} />, onSelect: () => nav(`/reports?run=${encodeURIComponent(runId)}${q(baseline)}`) },
    { label: "Start a study", icon: <Microscope size={14} />, onSelect: () => nav(`/studies?runs=${encodeURIComponent(runId)}${q(baseline)}`) },
    { label: "Dossier for the LLM", icon: <FileHeart size={14} />, disabled: dossier.busy, hint: "Everything found about this run, with evidence ids, for your local LLM", onSelect: () => void dossier.go() },
    { label: "Download a debug bundle", icon: <Download size={14} />, hint: "Plan a fast-debug download: review files and sizes, then start it", onSelect: () => void planAndOpen(nav, { run: runId, profile: "fast_debug" }).catch((e: unknown) => toast(String(e), "bad")) },
    ...(live || watching ? [{ label: watching ? "Stop watching" : "Watch this run", icon: watching ? <EyeOff size={14} /> : <Eye size={14} />,
      hint: "Tell me when it finishes, fails or develops a problem (a desktop notification when allowed)",
      onSelect: () => {
        if (watching) { setWatched(watched.filter((w) => w.ref !== runId)); toast("Stopped watching"); return; }
        setWatched([...watched, { ref: runId, name: r.display_name ?? r.name, status: r.status, state: verdictQ.data?.state ?? "" }]);
        toast("Watching — you'll hear when it finishes, fails or develops a problem");
        try { if ("Notification" in window && Notification.permission === "default") void Notification.requestPermission(); } catch { /* not available */ }
      } }] : []),
    { label: "", divider: true, onSelect: () => undefined },
    { label: "Copy link to this view", icon: <Link2 size={14} />, onSelect: () => { void navigator.clipboard.writeText(window.location.href); toast("Link copied"); } },
    { label: "Copy as mf command", icon: <Terminal size={14} />, hint: cli, onSelect: () => { void navigator.clipboard.writeText(cli); toast(`Copied: ${cli}`); } },
    ...(studio ? [{ label: "Open in Azure ML studio", icon: <ExternalLink size={14} />, onSelect: () => window.open(studio, "_blank", "noopener") }] : []),
    { label: "", divider: true, onSelect: () => undefined },
    { label: mode === "full" ? "Loading everything" : "Load everything now", icon: <Zap size={14} />, disabled: mode === "full", hint: "Metric histories, the baseline comparison and the code diff in the background", onSelect: () => setMode("full") },
    { label: showLoading ? "Hide loading details" : "Loading details", icon: <Activity size={14} />, onSelect: () => setShowLoading(!showLoading) },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <div className="row wrap" style={{ gap: 10 }}>
            <h1 className="ellipsis">{r.display_name ?? r.name}</h1>
            <StatusPill status={r.status} />
            {watching && <span className="tag" title="You'll hear when it finishes, fails or develops a problem" data-testid="watching"><Eye size={11} /> watching</span>}
            <LiveRefresh runId={runId} running={r.status === "running" || r.status === "queued"} local={r.tags?.source === "local-folder" || !!r.ref?.backend?.startsWith("local")} />
            <span className="tag">{r.kind}</span>
          </div>
          <div className="sub row wrap" style={{ gap: 10 }}>
            <span className="mono">{runId}</span>
            <button className="btn ghost sm icon" title="Copy id" onClick={() => { void navigator.clipboard.writeText(runId); toast("Copied run id"); }}><Copy size={12} /></button>
            <span>·</span><span>{r.experiment ?? "no experiment"}</span>
            <span>·</span><span>{r.user ?? "unknown user"}</span><AddToMe user={r.user} />
            <span>·</span><span>{fmtAgo(r.created_at)}</span>
            <span>·</span><span>{fmtDuration(r.started_at, r.ended_at)}</span>
          </div>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => nav(`/compare?candidate=${encodeURIComponent(runId)}${q(baseline)}`)}><GitCompare size={14} /> Compare</button>
          <button className="btn primary" onClick={() => set({ ask: "1" })} title="Ask about this run — grounded in its evidence"><Bot size={14} /> Ask</button>
          <Menu items={menu} />
        </div>
      </div>
      <ProgressLine active={active.length > 0} done={tasks.length - active.length} total={tasks.length} />
      {r.failure && (
        <div className="error-box" style={{ marginBottom: 14 }}>
          <ShieldAlert size={18} color="var(--bad)" style={{ flex: "none" }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>Failed{r.failure.category ? ` · ${r.failure.category}` : ""}</div>
            <div className="mono muted" style={{ whiteSpace: "pre-wrap" }}>{r.failure.message}</div>
          </div>
          <button className="btn sm" style={{ marginLeft: "auto" }} onClick={() => goView("logs")}>Open logs</button>
        </div>
      )}
      {(showLoading || tasks.some((t) => t.state === "failed")) && <RunLoadingPanel tasks={tasks} mode={mode} onMode={setMode}
        onLoadMetrics={() => void api.runLoad(runId, mode, ["run.metrics"]).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] }))} />}
      <Tabs<Group>
        value={group}
        onChange={(g) => goView(lastView(g))}
        right={runStages.length > 1 ? <span className="row" style={{ gap: 6 }} data-testid="run-stage" title="The pipeline stage the Metrics, Expert review and dossier read — stages are never mixed"><span className="faint" style={{ fontSize: 12 }}>Stage</span><StageSelect stages={runStages} value={stage} onChange={(v) => set({ stage: v || null })} /></span> : undefined}
        tabs={GROUPS.filter((g) => g.id === group || viewsOf(g.id).length > 0).map((g) => ({ id: g.id, label: g.label, icon: g.icon }))}
      />
      {viewsOf(group).length > 1 && (
        <div className="subtabs" role="tablist" aria-label={`${group} views`} data-testid="subtabs">
          {viewsOf(group).map((v) => (
            <button key={v} role="tab" aria-selected={v === tab} className="subtab" onClick={() => goView(v)}>
              {VIEW_LABEL[v]?.[1]}{VIEW_LABEL[v]?.[0] ?? v}{counts[v] !== undefined && <span className="count">{counts[v]}</span>}
            </button>
          ))}
        </div>
      )}
      {tab === "overview" && <OverviewTab top={<VerdictCard v={verdictQ.data} loading={verdictQ.isFetching && !!verdictQ.data && verdictQ.data.state === "not_loaded"}
          onOpen={(ks) => set({ tab: "metrics", mk: ks.join("|") })}
          onNext={(n) => { if (n.tab === "compare") nav(`/compare?candidate=${encodeURIComponent(runId)}${q(baseline)}`); else if (n.tab === "summary") document.querySelector(".what-changed input")?.scrollIntoView({ block: "center" }); else if (n.view) goView(n.view as Tab); else set({ tab: n.tab === "files" ? "logs" : n.tab ?? "review", mk: n.keys?.length ? n.keys.join("|") : null }); }}
          onLoad={() => void api.runLoad(runId, mode, ["run.metrics"]).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] }))}
          onBaseline={() => (document.querySelector(".what-changed input") as HTMLInputElement | null)?.focus()} />} stage={stage} o={ready.series ? o : { ...o, summary: light.data.summary_metrics }} headlineKeys={(ov.data?.headline_groups ?? light.data.headline_groups)?.keys ?? light.data.headline ?? []} headlineGroups={ov.data?.headline_groups ?? light.data.headline_groups ?? null}
        trends={trends.data} onShowTrends={() => void api.runLoad(runId, mode, ["run.metrics"]).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] }))} runId={runId} baseline={baseline} setBaseline={(b) => set({ baseline: b })} goTab={(t) => set({ tab: t })}
        full={mode === "full"} onLoadComparison={() => void api.runLoad(runId, mode, ["run.comparison"], baseline).then(() => qc.invalidateQueries({ queryKey: ["run-tasks", runId] }))} />}
      {tab === "review" && (series ? <ExpertReview runId={runId} baseline={baseline} stage={stage} onStage={(v) => set({ stage: v || null })} onPlot={(ks) => set({ tab: "metrics", mk: ks.join("|") })} /> : pending("run.metrics", "metric histories"))}
      {stage && runStages.includes(stage) && <StageScope tab={tab} stage={stage} stageRun={stageRun} runId={runId} alone={oneStage && STAGE_ALONE.has(tab)} loading={taskFor("run.metrics")?.active ?? false} onLoadAll={loadAll} />}
      {tab === "timeline" && <TimelineTab runId={runId} />}
      {tab === "plots" && (series ? <PlotsTab runId={runId} baseline={baseline} stage={stage} /> : pending("run.metrics", "metric histories"))}
      {tab === "uncertainty" && (series ? <UncertaintyTab runId={runId} stage={stage} /> : pending("run.metrics", "metric histories"))}
      {tab === "setup" && <SetupCluesTab key={stageRun} runId={stageRun} />}
      {tab === "metrics" && (ready.series ? <MetricsExplorer key={sp.get("mk") ?? ""} runId={runId} keys={o.metric_keys} baseline={baseline} initialPicked={sp.get("mk")?.split("|")} stage={stage} onStage={(v) => set({ stage: v || null })} />
        : oneStage && stageRun !== runId ? <MetricsExplorer key={`${stageRun}|${sp.get("mk") ?? ""}`} runId={stageRun} keys={[]} baseline={baseline && baselineParam === baseline ? baseStageRun : baseline} initialPicked={sp.get("mk")?.split("|")} />
        : pending("run.metrics", "metric histories"))}
      {tab === "findings" && <FindingsTab key={stageRun} runId={stageRun} baseline={baseline} autoRun={sp.get("analyze") === "1"} stored={stageRun === runId ? o.findings : []} />}
      {tab === "config" && <ConfigTab key={stageRun} runId={stageRun} baseline={baseline && baselineParam === baseline ? baseStageRun : baseline} />}
      {tab === "precision" && <PrecisionRecallTab runId={runId} stage={stage} />}
      {tab === "dataset" && <DatasetTab runId={runId} stage={stage} />}
      {tab === "compute" && <ComputeTab runId={runId} stage={stage} />}
      {tab === "logs" && <LogsTab key={`${runId}|${stage}`} runId={runId} stage={stage && runStages.includes(stage) ? stage : undefined} />}
      {tab === "sequences" && <SequencesTab key={stage} runId={runId} stage={stage} />}
      {tab === "artifacts" && (ready.artifacts ? <ArtifactsTab key={stage} runId={runId} artifacts={o.artifacts} stage={stage} /> : pending("run.artifacts", "file listing"))}
      {askOpen && <AskDrawer onClose={() => set({ ask: null, tab: asked === "ai" ? null : sp.get("tab") })}><InvestigationPanel run={runId} baseline={baseline} /></AskDrawer>}
    </div>
  );
}

/** Ask about the run without leaving the view: the investigation panel in a drawer from the right. */
function AskDrawer({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent): void => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Ask about this run" data-testid="ask-drawer">
        <div className="drawer-head"><Bot size={16} /><b>Ask about this run</b><span className="spacer" /><button className="btn ghost sm icon" aria-label="Close" onClick={onClose}><X size={14} /></button></div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

/** What a tab reads when a stage is picked — said in one line under the tabs, so the stage's reach is never a guess. */
const SCOPE: Partial<Record<Tab, "logged" | "run" | "opens" | "whole">> = {
  overview: "logged", review: "logged", metrics: "logged", plots: "logged", uncertainty: "logged",
  config: "run", logs: "run", findings: "run", setup: "run",
  precision: "opens", sequences: "opens", dataset: "opens", compute: "opens", artifacts: "opens",
  timeline: "whole", ai: "whole",
};

function StageScope({ tab, stage, stageRun, runId, alone, loading, onLoadAll }: { tab: Tab; stage: string; stageRun: string; runId: string; alone: boolean; loading: boolean; onLoadAll: () => void }) {
  const kind = SCOPE[tab] ?? "whole";
  const text = {
    logged: <>what <b>{stage}</b> logged — other stages are not mixed in</>,
    run: <>the run of <b>{stage}</b> (<span className="mono">{stageRun === runId ? "loading…" : stageRun}</span>)</>,
    opens: <><b>{stage}</b> first — the other stages are one click away here</>,
    whole: <>the whole pipeline (this view is about all stages together)</>,
  }[kind];
  return (
    <div className="stage-scope faint row" data-testid="stage-scope" data-scope={kind} style={{ gap: 8 }}>
      <span>Stage <b>{stage}</b> · this tab shows {text}</span>
      {alone && <span className="row" style={{ gap: 6, marginLeft: "auto" }} data-testid="stage-alone">
        <span title="Only this stage's metric histories were fetched — the view opens without waiting for the other stages">read alone (fast)</span>
        <button className="btn sm" disabled={loading} onClick={onLoadAll} title="Fetch every stage's metric histories (for the stage chips, the headline and all-stage views)">{loading ? "loading all stages…" : "Load all stages"}</button>
      </span>}
    </div>
  );
}

/** Tabs that read one stage on its own when the page has a stage (the pipeline's other stages are not fetched). */
const STAGE_ALONE = new Set<Tab>(["review", "metrics", "plots", "uncertainty", "setup", "findings"]);

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
