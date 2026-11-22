import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Copy, LineChart, ArrowDown, ArrowUp, CheckCircle2, CloudDownload, Columns3, Download, GitCompare, Info, Microscope, RefreshCw, Search, Star, TrendingDown, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, refStr } from "../api/client";
import type { Job, Run } from "../api/types";
import { useApp } from "../components/AppState";
import { Empty, ErrorBox, Loading, StatusPill } from "../components/ui";
import { fmtAgo, fmtDuration, fmtNum, shortSha } from "../lib/format";
import { useLocalStorage } from "../lib/hooks";

const QUICK = [
  { label: "All", q: "" },
  { label: "Mine", q: "user:me" },
  { label: "My experiments", q: "experiment:mine" },
  { label: "Failed", q: "status:failed" },
  { label: "Running", q: "status:running" },
  { label: "Last 7 days", q: "since:7d" },
  { label: "Baselines", q: "tag:role=baseline" },
  { label: "Favorites", q: "is:fav" },
];
const EXAMPLES = ["failed training jobs from last week", "show runs using 8-frame temporal windows", "NDS>0.6 tag:role=baseline", "user:me status:failed", "user:sam branch:feat/radar-fusion"];

type SortKey = "name" | "id" | "status" | "experiment" | "user" | "created" | "duration" | `m:${string}`;

export function RunsPage() {
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const [draft, setDraft] = useState(q);
  const [selected, setSelected] = useState<string[]>([]);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "created", dir: -1 });
  const [metricCols, setMetricCols] = useLocalStorage<string[] | null>("mf.runs.metricCols", null);
  const [colPicker, setColPicker] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();
  const { toast, workspace } = useApp();
  const res = useQuery({ queryKey: ["runs", "explorer", q, workspace], queryFn: () => api.search(q, 500, 0, workspace), placeholderData: keepPreviousData });
  const runs = res.data?.runs ?? [];
  const { jobs } = useApp();
  const experiments = useQuery({ queryKey: ["experiments", workspace], queryFn: () => api.experiments(workspace), staleTime: 60_000 });
  // Metadata warm-up: the state comes from the server's jobs (latest warm-up per run), so it survives page changes
  // and reloads; the wheel turns while the job runs, a tick shows warm runs.
  const [starting, setStarting] = useState<Record<string, boolean>>({});
  const ids = useMemo(() => runs.map((r) => refStr(r.ref)), [runs]);
  const cached = useQuery({ queryKey: ["warm", ids], queryFn: () => api.warmRuns(ids), enabled: ids.length > 0, staleTime: 30_000 });
  const preJobs = useMemo(() => {
    const m: Record<string, Job> = {};
    for (const j of jobs) if (j.kind === "warm-up" && j.run && (!m[j.run] || m[j.run]!.created_at < j.created_at)) m[j.run] = j;
    return m;
  }, [jobs]);
  const finished = Object.values(preJobs).filter((j) => j.status !== "running").length;
  useEffect(() => { if (finished) void qc.invalidateQueries({ queryKey: ["warm"] }); }, [finished, qc]);
  const startWarmUp = (id: string, force = false): void => {
    setStarting((s) => ({ ...s, [id]: true }));
    void api.warmUp(id, force).then(() => qc.invalidateQueries({ queryKey: ["jobs"] })).finally(() => setStarting((s) => ({ ...s, [id]: false })));
  };
  const busy = Object.values(preJobs).filter((j) => j.status === "running").length + Object.values(starting).filter(Boolean).length;

  // Metric columns come from the data (ranked on the server, a project headline first) — no metric name is hard-coded.
  const mc = useQuery({ queryKey: ["metric-columns", ids], queryFn: () => api.metricColumns(ids), enabled: ids.length > 0, staleTime: 15_000 });
  const allMetrics = useMemo(() => (mc.data?.columns ?? []).map((c) => c.key), [mc.data]);
  const cols = (metricCols ?? mc.data?.default ?? []).filter((m) => allMetrics.includes(m) || (metricCols ?? []).includes(m));
  const [filling, setFilling] = useState(0);
  const fillColumns = (missing: string[]): void => {
    void api.fillSummaries(missing).then((r) => { setFilling(r.tasks.filter((t) => t.active).length || r.tasks.length); toast(`Loading metric values of ${r.tasks.length} run(s) in the background`); });
  };
  const fillTasks = useQuery({
    queryKey: ["fill-tasks"], queryFn: api.tasksActive, enabled: filling > 0,
    refetchInterval: (q) => ((q.state.data?.active ?? []).some((t) => t.title === "Metric column values") ? 1000 : false),
  });
  const stillFilling = (fillTasks.data?.active ?? []).filter((t) => t.title === "Metric column values").length;
  useEffect(() => {
    if (filling > 0 && fillTasks.data && stillFilling === 0) {
      setFilling(0);
      void qc.invalidateQueries({ queryKey: ["runs"] });
      void qc.invalidateQueries({ queryKey: ["metric-columns"] });
    }
  }, [filling, stillFilling, fillTasks.data, qc]);

  const sorted = useMemo(() => {
    const val = (r: Run): string | number => {
      const k = sort.key;
      if (k.startsWith("m:")) return r.summary_metrics[k.slice(2)] ?? -Infinity;
      if (k === "id") return refStr(r.ref);
      if (k === "created") return r.created_at ? new Date(r.created_at).getTime() : 0;
      if (k === "duration") return r.started_at && r.ended_at ? new Date(r.ended_at).getTime() - new Date(r.started_at).getTime() : 0;
      return String((r as unknown as Record<string, unknown>)[k] ?? "");
    };
    return [...runs].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * sort.dir);
  }, [runs, sort]);

  const submit = (v: string): void => { setDraft(v); setSp(v ? { q: v } : {}); };
  const toggle = (id: string): void => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const fav = async (r: Run): Promise<void> => {
    const isFav = !!r.favorite;
    await api.favorite(refStr(r.ref), !isFav);
    void qc.invalidateQueries({ queryKey: ["runs"] });
    toast(isFav ? "Removed from favorites" : "Added to favorites");
  };
  const th = (key: SortKey, label: string, right = false, raw = false) => (
    <th className={`sortable ${right ? "r" : ""}`} style={raw ? { textTransform: "none" } : undefined} onClick={() => setSort((s) => ({ key, dir: s.key === key ? ((-s.dir) as 1 | -1) : -1 }))}>
      <span className="row" style={{ gap: 4, justifyContent: right ? "flex-end" : undefined }}>{label}{sort.key === key && (sort.dir === 1 ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}</span>
    </th>
  );

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Runs</h1><div className="sub">Search by name, filters, metric ranges, config values — or plain English.</div></div>
      </div>

      <form onSubmit={(e) => { e.preventDefault(); submit(draft); }} className="row" style={{ marginBottom: 10 }}>
        <div className="input-wrap" style={{ flex: 1 }}>
          <Search size={15} />
          <input className="input mono" style={{ height: 38, fontSize: 13 }} value={draft} onChange={(e) => setDraft(e.target.value)}
            placeholder='status:failed exp:sparse4d-fusion tag:role=baseline cfg:temporal.num_frames=8 NDS>0.6 since:7d   — or —   "failed training jobs from last week"' />
        </div>
        {draft && <button type="button" className="btn ghost icon" onClick={() => submit("")}><X size={15} /></button>}
        <button className="btn primary" style={{ height: 38 }}>Search</button>
      </form>
      <div className="row wrap" style={{ marginBottom: 12 }}>
        {QUICK.map((f) => <button key={f.label} className={`btn sm ${q === f.q ? "primary" : ""}`} onClick={() => submit(f.q)}>{f.label}</button>)}
        <select className="input" aria-label="Experiment" style={{ height: 28, width: 180, fontSize: 12.5 }} value={/^experiment:"?([^"]+)"?$/.exec(q)?.[1] ?? ""}
          onChange={(e) => submit(e.target.value ? (e.target.value === "mine" ? "experiment:mine" : `experiment:"${e.target.value}"`) : "")}>
          <option value="">Experiment…</option>
          {(experiments.data ?? []).map((x) => <option key={x.name} value={x.name}>{x.mine ? "★ " : ""}{x.name} ({x.runs})</option>)}
        </select>
        <span className="spacer" />
        {busy > 0 && <span className="row faint" style={{ gap: 4, fontSize: 12 }}><RefreshCw size={12} className="spin" /> warming up {busy}</span>}
        {res.data && <span className="faint">{res.data.total} runs · {res.data.took_ms} ms</span>}
        <div style={{ position: "relative" }}>
          <button className="btn sm" onClick={() => setColPicker(!colPicker)}><Columns3 size={13} /> Metric columns</button>
          {colPicker && (
            <div className="palette" style={{ position: "absolute", right: 0, top: 32, width: 280, zIndex: 30 }}>
              <div className="palette-list" style={{ maxHeight: 320 }}>
                {allMetrics.map((m) => (
                  <label key={m} className="metric-row">
                    <input type="checkbox" checked={cols.includes(m)} onChange={() => setMetricCols(cols.includes(m) ? cols.filter((c) => c !== m) : [...cols, m])} />
                    <span className="mono">{m}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
      {mc.data?.diagnosis.filter((d) => d.runs_without_values > 0).map((d) => (
        <div key={d.backend} className="note" data-testid="metric-columns-note" style={{ marginBottom: 8 }}>
          <Info size={14} style={{ marginTop: 2 }} />
          <span><b>Metric columns · {d.backend}:</b> {d.runs - d.runs_without_values}/{d.runs} runs have values — {d.cause}.</span>
          <span className="spacer" />
          {d.fillable && (filling > 0
            ? <span className="row faint" style={{ gap: 4 }}><RefreshCw size={12} className="spin" /> filling {stillFilling || filling}…</span>
            : <button className="btn sm" onClick={() => fillColumns(d.missing)}><Columns3 size={13} /> Fill metric columns</button>)}
        </div>
      ))}
      {res.data?.notes.map((n) => <div key={n} className="note" style={{ marginBottom: 8 }}><Info size={14} style={{ marginTop: 2 }} /><span className="mono">{n}</span></div>)}

      {selected.length > 0 && (
        <div className="card row" style={{ padding: "8px 12px", marginBottom: 10, position: "sticky", top: 0, zIndex: 5, borderColor: "var(--accent)" }}>
          <b>{selected.length} selected</b>
          <span className="spacer" />
          <button className="btn sm" disabled={selected.length < 2 || selected.length > 10} title="2–10 runs; the first selected is the reference" onClick={() => nav(`/compare?baseline=${encodeURIComponent(selected[0]!)}&candidate=${encodeURIComponent(selected[1]!)}${selected.length > 2 ? `&more=${encodeURIComponent(selected.slice(2, 10).join(","))}` : ""}`)}>
            <GitCompare size={13} /> Compare {selected.length > 2 ? `${selected.length} runs` : ""} (first = reference)
          </button>
          <button className="btn sm primary" onClick={() => nav(`/studies?runs=${encodeURIComponent(selected.join(","))}`)}><Microscope size={13} /> Start study</button>
          <button className="btn sm" onClick={() => nav(`/progression?runs=${encodeURIComponent(selected.join(","))}`)}><TrendingDown size={13} /> Plot</button>
          <button className="btn sm" title="Load the metric histories of these runs in the background (the heavy part on Azure ML) — watch them in the task indicator"
            onClick={() => void api.loadMetrics(selected).then((r) => toast(`Loading metrics of ${r.tasks.length} run(s) in the background`))}><LineChart size={13} /> Load metrics</button>
          <button className="btn sm" title="Load stages, configs, metrics and file lists so these runs open fast (no artifact download)" onClick={() => selected.forEach((id) => startWarmUp(id))}><CloudDownload size={13} /> Warm up</button>
          <button className="btn sm" title="Plan a fast-debug prefetch per run — review sizes and files, then start it on the Prefetch page" onClick={() => { void Promise.all(selected.map((id) => api.prefetchPlan({ run: id, profile: "fast_debug" }))).then(() => nav("/prefetch")); }}><Download size={13} /> Plan prefetch</button>
          <button className="btn sm ghost" onClick={() => setSelected([])}>Clear</button>
        </div>
      )}

      <div className="card">
        {res.error ? <div className="card-body"><ErrorBox error={res.error} /></div> : res.isLoading ? <Loading /> : runs.length === 0 ? (
          <Empty title="No runs match" icon={<Search size={26} />}>
            Try one of: {EXAMPLES.map((e) => <button key={e} className="chip" style={{ margin: 3 }} onClick={() => submit(e)}>{e}</button>)}
          </Empty>
        ) : (
          <div className="table-wrap" style={{ maxHeight: "calc(100vh - 290px)" }}>
            <table className="t">
              <thead>
                <tr>
                  <th style={{ width: 30 }} />
                  {th("name", "Run")}{th("id", "Run ID")}{th("status", "Status")}{th("experiment", "Experiment")}{th("user", "User")}
                  <th>Git</th><th>Compute</th>
                  {cols.map((m) => <span key={m} style={{ display: "contents" }}>{th(`m:${m}`, m, true, true)}</span>)}
                  {th("duration", "Duration", true)}{th("created", "Created", true)}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => {
                  const id = refStr(r.ref);
                  return (
                    <tr key={id} className={`clickable ${selected.includes(id) ? "selected" : ""}`} onClick={() => nav(`/runs/${encodeURIComponent(id)}`)}>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="row" style={{ gap: 6 }}>
                          <input type="checkbox" checked={selected.includes(id)} onChange={() => toggle(id)} style={{ accentColor: "var(--accent)" }} />
                          <Star size={13} style={{ cursor: "pointer" }} fill={r.favorite ? "var(--warn)" : "none"} color={r.favorite ? "var(--warn)" : "var(--text-3)"} onClick={() => void fav(r)} />
                          <WarmUpButton job={starting[id] ? null : preJobs[id]} cached={!!cached.data?.[id]} onStart={(force) => startWarmUp(id, force)} />
                        </div>
                      </td>
                      <td className="wrap" style={{ maxWidth: 320, minWidth: 220 }}>
                        <div style={{ fontWeight: 550 }} className="ellipsis">{r.display_name ?? r.name}</div>
                        <div className="row wrap" style={{ gap: 4, marginTop: 2 }}>
                          {!workspace && <span className="tag" style={{ background: "var(--accent-bg)", color: "var(--accent-2)" }} title="Workspace">{r.ref.backend}</span>}
                          {Object.entries(r.tags).slice(0, 3).map(([k, v]) => <span key={k} className="tag">{k}={v}</span>)}
                        </div>
                      </td>
                      <td className="run-id" onClick={(e) => e.stopPropagation()}>
                        <span className="row" style={{ gap: 4 }}>
                          <span className="mono faint selectable ellipsis" style={{ maxWidth: 200 }} title={id}>{r.ref.run_id}</span>
                          <button type="button" className="icon-btn" aria-label={`Copy run id ${r.ref.run_id}`} title="Copy run id" onClick={() => { void navigator.clipboard?.writeText(r.ref.run_id); toast("Copied run id"); }}><Copy size={11} /></button>
                        </span>
                      </td>
                      <td><StatusPill status={r.status} /></td>
                      <td className="muted">{r.experiment ?? "—"}</td>
                      <td className="muted">{r.user ?? "—"}</td>
                      <td><span className="mono faint">{r.git.commit ? `${r.git.branch ?? "?"}@${shortSha(r.git.commit)}` : "—"}</span></td>
                      <td className="muted">{r.compute.target ?? "—"}{r.compute.gpu_count ? <span className="faint"> · {r.compute.gpu_count}×{r.compute.gpu_type ?? "GPU"}</span> : null}</td>
                      {cols.map((m) => <td key={m} className="r num">{fmtNum(r.summary_metrics[m])}</td>)}
                      <td className="r num muted">{fmtDuration(r.started_at, r.ended_at)}</td>
                      <td className="r faint">{fmtAgo(r.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

/** Warm up a run's metadata (stages, configs, metrics, file list) so its page opens fast — not an artifact download. Idle: a cloud; working: a
 *  turning wheel; done: a tick (click again to refresh); failed: a red mark with the reason. */
function WarmUpButton({ job, cached, onStart }: { job: Job | null | undefined; cached: boolean; onStart: (force: boolean) => void }) {
  const running = job === null || job?.status === "running";
  const failed = job && (job.status === "failed" || job.status === "interrupted" || job.status === "cancelled");
  const step = (job?.progress as { step?: string } | undefined)?.step;
  const [Icon, color, title, cls] = running
    ? [RefreshCw, "var(--accent)", `Warming up${step ? ` — ${step}` : "…"}`, "spin"]
    : failed
      ? [AlertCircle, "var(--bad)", `Warm-up failed: ${job?.error ?? job?.status} — click to retry`, ""]
      : cached || job?.status === "done"
        ? [CheckCircle2, "var(--good)", "Metadata cached — the run opens fast. Click to refresh it from the backend.", ""]
        : [CloudDownload, "var(--text-3)", "Warm up: load metadata (stages, configs, metrics, file list) so the run opens fast — no artifact download", ""];
  return (
    <button type="button" className="icon-btn warm-btn" aria-label={title} title={title} disabled={running}
      onClick={() => onStart(!!(cached || job?.status === "done"))}>
      <Icon size={13} color={color} className={cls} />
    </button>
  );
}
