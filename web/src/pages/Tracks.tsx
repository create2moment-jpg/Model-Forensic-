/** Continuous training: the experiments you follow, run after run — each one an experiment of a workspace (typically the
 *  continuous-training workspace), picked from two pull-down menus; several at once. A track's page answers, for every
 *  run: better than the previous one? than the best so far? (noise-aware, the verdict's rule) — and how long it took. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Eye, EyeOff, GitCompare, ListChecks, Plus, RefreshCw, Trash2, Trophy } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Track, TrackCompare, TrackProgress, TrackRun } from "../api/types";
import { fmtSecs, TrackStateBadge } from "../components/TrackState";
import { useApp } from "../components/AppState";
import { Chart, PALETTE, type ChartSeries } from "../components/Chart";
import { Card, Empty, ErrorBox, Loading, StatusPill } from "../components/ui";
import { fmtAgo, fmtNum } from "../lib/format";

/** Workspace and experiment, each a pull-down menu (the experiment list follows the workspace picked). */
function AddTrack({ existing, onAdded }: { existing: Track[]; onAdded: (t: Track) => void }) {
  const { toast } = useApp();
  const backends = useQuery({ queryKey: ["backends"], queryFn: api.backends, staleTime: 60_000 });
  const [ws, setWs] = useState("");
  const [exp, setExp] = useState("");
  const [title, setTitle] = useState("");
  const exps = useQuery({ queryKey: ["experiments", ws, "all-projects"], queryFn: () => api.experiments(ws, true), enabled: !!ws, staleTime: 60_000 });
  const taken = new Set(existing.filter((t) => t.workspace === ws).map((t) => t.experiment));
  const add = useMutation({
    mutationFn: () => api.trackCreate({ workspace: ws, experiment: exp, title: title.trim() || undefined }),
    onSuccess: (t) => { setExp(""); setTitle(""); toast(`Following ${t.experiment}`); onAdded(t); },
    onError: (e) => toast(String(e), "bad"),
  });
  return (
    <form className="track-add" data-testid="track-add" onSubmit={(e) => { e.preventDefault(); if (ws && exp) add.mutate(); }}>
      <label className="field">Workspace
        <select className="select" aria-label="Workspace" value={ws} onChange={(e) => { setWs(e.target.value); setExp(""); }}>
          <option value="">Pick a workspace…</option>
          {(backends.data ?? []).map((b) => <option key={b.name} value={b.name}>{b.title || b.name}{b.workspace && b.workspace !== b.title ? ` (${b.workspace})` : ""}</option>)}
        </select>
      </label>
      <label className="field">Experiment
        <select className="select" aria-label="Experiment to follow" value={exp} disabled={!ws || exps.isLoading} onChange={(e) => setExp(e.target.value)}>
          <option value="">{!ws ? "Pick a workspace first" : exps.isLoading ? "Reading the experiments…" : (exps.data ?? []).length ? "Pick an experiment…" : "No experiments indexed — sync the workspace"}</option>
          {(exps.data ?? []).map((x) => <option key={x.name} value={x.name} disabled={taken.has(x.name)}>{x.name} · {x.runs} run{x.runs === 1 ? "" : "s"}{x.last ? ` · last ${fmtAgo(x.last)}` : ""}{taken.has(x.name) ? " · followed" : ""}</option>)}
        </select>
      </label>
      <label className="field">Name (optional)
        <input className="input" aria-label="Track name" placeholder={exp || "e.g. Nightly detector"} value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <button className="btn primary" disabled={!ws || !exp || add.isPending}><Plus size={14} /> Follow</button>
    </form>
  );
}

/** The primary metric over the track's runs, small, for the list. */
function Mini({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["track", id], queryFn: () => api.track(id), staleTime: 30_000 });
  const p = q.data;
  const k = p?.primary;
  const pts = (p?.runs ?? []).filter((r) => k && r.values[k] !== undefined).map((r) => r.values[k!]!);
  if (pts.length < 2) return <span className="faint" style={{ fontSize: 12 }}>{q.isLoading ? "…" : "too few values for a curve"}</span>;
  const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1, w = 180, h = 36;
  const xy = pts.map((v, i) => `${(2 + (i * (w - 4)) / (pts.length - 1)).toFixed(1)},${(h - 3 - ((v - lo) / span) * (h - 6)).toFixed(1)}`);
  return (
    <svg width={w} height={h} role="img" aria-label={`${k} over ${pts.length} runs`}>
      <polyline points={xy.join(" ")} fill="none" stroke="var(--accent)" strokeWidth={1.6} strokeLinejoin="round" />
      <circle cx={xy.at(-1)!.split(",")[0]} cy={xy.at(-1)!.split(",")[1]} r={2.6} fill="var(--accent)" />
    </svg>
  );
}

export function TracksPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { later, pending } = useApp();
  const list = useQuery({ queryKey: ["tracks", "progress"], queryFn: () => api.tracks(true) });
  const tracks = (list.data?.tracks ?? []).filter((t) => !pending(`track:${t.id}`));
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Continuous training</h1>
          <div className="sub">Follow recurring training runs — an experiment of a workspace — and see whether each new model beats the previous one and the best so far.</div>
        </div>
      </div>
      <Card title="Follow an experiment" sub="pick the continuous-training workspace and one of its experiments — follow as many as you like">
        <AddTrack existing={list.data?.tracks ?? []} onAdded={(t) => { void qc.invalidateQueries({ queryKey: ["tracks"] }); nav(`/tracks/${encodeURIComponent(t.id)}`); }} />
      </Card>
      <div style={{ height: 14 }} />
      {list.error ? <ErrorBox error={list.error} /> : list.isLoading ? <Loading /> : tracks.length === 0 ? (
        <Card><Empty title="Nothing followed yet" icon={<Trophy size={24} />}>Pick a workspace and an experiment above — every run it gets is compared with the previous one and the best so far.</Empty></Card>
      ) : (
        <div className="track-grid" data-testid="track-list">
          {tracks.map((t) => (
            <div key={t.id} className="card track-card" data-track={t.id}>
              <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <Link to={`/tracks/${encodeURIComponent(t.id)}`} className="track-title">{t.title}</Link>
                  <div className="faint ellipsis" style={{ fontSize: 12 }}>{t.workspace} · {t.experiment} · {t.health?.runs ?? 0} runs</div>
                </div>
                <TrackStateBadge state={t.state} />
              </div>
              <div className="muted" style={{ fontSize: 13, margin: "8px 0" }}>{t.sentence}</div>
              <div className="row" style={{ gap: 8 }}>
                <Mini id={t.id} />
                <span className="spacer" />
                <button className="icon-btn" aria-label={`Stop following ${t.title}`} title="Stop following (the runs stay where they are)"
                  onClick={() => later(`track:${t.id}`, `Stopped following ${t.title}`, () => api.trackDelete(t.id).then(() => qc.invalidateQueries({ queryKey: ["tracks"] })))}><Trash2 size={13} /></button>
                <Link className="btn ghost sm" to={`/tracks/${encodeURIComponent(t.id)}`}>Open <ArrowRight size={13} /></Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** What changed against the previous finished run: code, data versions, environment, parameters, compute — from what
 *  the index holds (the config files' diff is one click away in Compare). */
function Changes({ c, logs }: { c: { what: string; before: string; after: string }[]; logs: string[] }) {
  if (!c.length && !logs.length) return <span className="faint">—</span>;
  return (
    <div className="track-changes" data-testid="track-changes">
      {c.slice(0, 4).map((x) => (
        <span key={x.what} className="change-chip" title={`${x.what}: ${x.before} → ${x.after}`}><b>{x.what}</b> {x.before} → {x.after}</span>
      ))}
      {c.length > 4 && <span className="faint" title={c.slice(4).map((x) => `${x.what}: ${x.before} → ${x.after}`).join("\n")}>+{c.length - 4} more</span>}
      {logs.map((l) => <span key={l} className="change-chip bad" title="from the run's logs">{l}</span>)}
    </div>
  );
}

/** Which metrics decide "better" for this track: the project's headline metrics (default) or the ones picked here —
 *  the first one is the one the best run is chosen by. */
function MetricPicker({ track, available, current }: { track: Track; available: string[]; current: string[] }) {
  const qc = useQueryClient();
  const { toast } = useApp();
  const [open, setOpen] = useState(false);
  const [pick, setPick] = useState<string[]>(track.metrics.length ? track.metrics : current);
  const [filter, setFilter] = useState("");
  const save = (metrics: string[]): void => {
    void api.trackUpdate(track.id, { metrics }).then(() => { setOpen(false); toast(metrics.length ? `Judged by ${metrics[0]}${metrics.length > 1 ? ` (+${metrics.length - 1})` : ""}` : "Judged by the project's headline metrics"); void qc.invalidateQueries({ queryKey: ["track", track.id] }); void qc.invalidateQueries({ queryKey: ["tracks"] }); });
  };
  const shown = [...new Set([...pick, ...available])].filter((k) => !filter || k.toLowerCase().includes(filter.toLowerCase()));
  return (
    <div style={{ position: "relative" }}>
      <button className="btn" onClick={() => { setPick(track.metrics.length ? track.metrics : current); setOpen(!open); }} aria-expanded={open} data-testid="track-metrics-btn"><ListChecks size={14} /> Metrics</button>
      {open && (
        <div className="palette track-metric-picker" data-testid="track-metric-picker">
          <div className="faint" style={{ fontSize: 12, padding: "8px 10px 4px" }}>Which metrics decide “better” — the first picked chooses the best run.</div>
          <div style={{ padding: "0 10px 6px" }}><input className="input" aria-label="Filter metrics" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
          <div className="palette-list" style={{ maxHeight: 280 }}>
            {shown.map((k) => (
              <label key={k} className="metric-row">
                <input type="checkbox" checked={pick.includes(k)} onChange={() => setPick(pick.includes(k) ? pick.filter((x) => x !== k) : [...pick, k])} />
                <span className="mono">{k}</span>{pick[0] === k && <span className="pill good" style={{ marginLeft: 6 }}>decides best</span>}
              </label>
            ))}
          </div>
          <div className="row" style={{ gap: 6, padding: 10, borderTop: "1px solid var(--line)" }}>
            <button className="btn sm ghost" onClick={() => save([])}>Use the headline metrics</button>
            <span className="spacer" />
            <button className="btn sm" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn sm primary" disabled={!pick.length} onClick={() => save(pick)}>Save</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Delta({ c, k }: { c: TrackCompare | null; k: string }) {
  const d = c?.metrics[k];
  if (!d || d.delta_pct === null) return null;
  const tone = d.noise ? "var(--text-3)" : d.good === true ? "var(--good)" : d.good === false ? "var(--bad)" : "var(--text-3)";
  return <span className="num" style={{ color: tone, fontSize: 11.5 }} title={d.noise ? "within noise" : undefined}> {d.delta_pct > 0 ? "+" : ""}{d.delta_pct.toFixed(1)}%</span>;
}

const VS: Record<string, string> = { better: "better", worse: "worse", mixed: "mixed", same: "same", no_data: "—" };

function VsPill({ c }: { c: TrackCompare | null }) {
  if (!c) return <span className="faint">—</span>;
  const tone = c.state === "better" ? "good" : c.state === "worse" ? "bad" : c.state === "mixed" ? "warn" : "";
  return <span className={`pill ${tone}`} title={`better on ${c.better}, worse on ${c.worse}, within noise on ${c.within_noise}`}>{VS[c.state]}</span>;
}

export function TrackPage() {
  const id = decodeURIComponent(useParams().trackId ?? "");
  const qc = useQueryClient();
  const nav = useNavigate();
  const { toast, later } = useApp();
  // while values load (run.summary tasks), read the track again every 3 s; while a run is running, every minute
  const [filling, setFilling] = useState(0);
  const q = useQuery({ queryKey: ["track", id], queryFn: () => api.track(id),
    refetchInterval: (x) => (filling && Date.now() - filling < 120_000 && (x.state.data?.missing_values.length ?? 0) > 0 ? 3000 : (x.state.data?.health.running ?? 0) > 0 ? 60_000 : false) });
  // finished runs without values: load them once when the track is opened (finished runs are cached for good)
  const started = useRef("");
  const missing = q.data?.missing_values ?? [];
  useEffect(() => {
    if (!missing.length || started.current === id) return;
    started.current = id;
    setFilling(Date.now());
    void api.fillSummaries(missing).catch(() => setFilling(0));
  }, [id, missing]);
  const p: TrackProgress | undefined = q.data;
  const charts = useMemo(() => (p ? p.metrics.map((m) => {
    const pts = p.runs.map((r, i) => ({ i: i + 1, v: r.values[m.key] })).filter((x) => x.v !== undefined);
    const best: (number | null)[] = [];
    let b: number | null = null;
    for (const x of pts) { b = b === null ? x.v! : m.higher_is_better === false ? Math.min(b, x.v!) : Math.max(b, x.v!); best.push(b); }
    const series: ChartSeries[] = [
      { name: m.label, x: pts.map((x) => x.i), y: pts.map((x) => x.v!), color: PALETTE[0], width: 2 },
      ...(m.higher_is_better !== null ? [{ name: "best so far", x: pts.map((x) => x.i), y: best, color: PALETTE[1], dash: true }] : []),
    ];
    return { m, series, n: pts.length };
  }) : []), [p]);
  if (q.error) return <div className="page"><ErrorBox error={q.error} /></div>;
  if (!p) return <div className="page"><Loading label="Reading the track…" /></div>;
  const t = p.track;
  const runs = [...p.runs].reverse();  // newest first in the table
  const fill = (): void => { setFilling(Date.now()); void api.fillSummaries(p.missing_values).then((r) => toast(`Loading the values of ${r.tasks.length} run(s) in the background — the track updates as they arrive`)).catch((e: unknown) => { setFilling(0); toast(String(e), "bad"); }); };
  const loadingValues = !!filling && Date.now() - filling < 120_000;
  const latest = p.latest, best = p.best;
  return (
    <div className="page" data-testid="track-page">
      <div className="page-head">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="faint" style={{ fontSize: 12 }}><Link to="/tracks">Continuous training</Link> · {t.workspace} · {t.experiment}</div>
          <h1 className="row" style={{ gap: 10 }}>{t.title} <TrackStateBadge state={p.state} /></h1>
          <div className="sub" data-testid="track-sentence">{p.sentence}</div>
        </div>
        <div className="actions" style={{ flexWrap: "nowrap", flex: "none" }}>
          <MetricPicker track={t} available={p.available_metrics ?? []} current={p.metrics.map((m) => m.key)} />
          {latest && best && latest !== best && <button className="btn" onClick={() => nav(`/compare?baseline=${encodeURIComponent(best)}&candidate=${encodeURIComponent(latest)}`)}><GitCompare size={14} /> Latest vs best</button>}
          <button className="btn" aria-pressed={t.watch} title={t.watch ? "On Today, and you are told when a new run fails, falls below the best or is the new best — click to stop" : "Show this track on Today and be told when a new run fails, falls below the best or is the new best"}
            onClick={() => void (!t.watch && "Notification" in window && Notification.permission === "default" ? Notification.requestPermission().catch(() => undefined) : Promise.resolve()).then(() => api.trackUpdate(t.id, { watch: !t.watch })).then(() => qc.invalidateQueries({ queryKey: ["track", id] }).then(() => qc.invalidateQueries({ queryKey: ["tracks"] })))}>
            {t.watch ? <><Eye size={14} /> On Today</> : <><EyeOff size={14} /> Not on Today</>}
          </button>
          <button className="btn ghost" aria-label="Stop following" title="Stop following (the runs stay where they are)" onClick={() => { later(`track:${t.id}`, `Stopped following ${t.title}`, () => api.trackDelete(t.id).then(() => qc.invalidateQueries({ queryKey: ["tracks"] }))); nav("/tracks"); }}><Trash2 size={14} /></button>
        </div>
      </div>

      <div className="track-stats" data-testid="track-health">
        <div><b>{p.health.runs}</b><span>runs</span></div>
        <div><b>{p.health.completed}</b><span>finished</span></div>
        <div><b style={{ color: p.health.failed ? "var(--bad)" : undefined }}>{p.health.failed}</b><span>failed</span></div>
        <div><b>{p.health.running}</b><span>running</span></div>
        <div><b>{fmtSecs(p.health.median_run_s)}</b><span>typical run time</span></div>
        <div><b style={{ color: p.health.latest_run_s && p.health.median_run_s && p.health.latest_run_s > 1.5 * p.health.median_run_s ? "var(--warn)" : undefined }}>{fmtSecs(p.health.latest_run_s)}</b><span>latest run time</span></div>
      </div>

      {p.missing_values.length > 0 && (
        <div className="note row" style={{ margin: "12px 0", gap: 8 }} data-testid="track-missing">
          <span>{loadingValues ? `Loading the metric values of ${p.missing_values.length} finished run${p.missing_values.length === 1 ? "" : "s"} — the track fills in as they arrive.` : `${p.missing_values.length} finished run${p.missing_values.length === 1 ? " has" : "s have"} no metric values yet (the workspace's job listing carries none).`}</span>
          <span className="spacer" />
          <button className="btn sm" disabled={loadingValues} onClick={fill}><RefreshCw size={13} className={loadingValues ? "spin" : ""} /> {loadingValues ? "Loading…" : "Load values"}</button>
        </div>
      )}

      {charts.some((c) => c.n >= 2) && (
        <div className="grid g2" style={{ margin: "14px 0" }}>
          {charts.filter((c) => c.n >= 2).map(({ m, series }) => (
            <Card key={m.key} title={m.label} sub={m.higher_is_better === false ? "lower is better · by run, oldest first" : m.higher_is_better ? "higher is better · by run, oldest first" : "by run, oldest first"} flush>
              <div style={{ padding: "4px 8px 8px" }}><Chart series={series} height={200} xLabel="run" showRaw={false} /></div>
            </Card>
          ))}
        </div>
      )}

      <Card title="Runs" sub="newest first · each vs the previous finished run and vs the best so far (beyond noise)" flush>
        <div className="table-wrap">
          <table className="t" data-testid="track-runs">
            <thead><tr><th>Run</th><th>Status</th>{p.metrics.map((m) => <th key={m.key} className="r" title={m.key}>{m.label}</th>)}<th>vs previous</th><th>vs best</th><th className="r">Run time</th><th className="r">Queued</th><th>What changed</th></tr></thead>
            <tbody>
              {runs.map((r: TrackRun) => (
                <tr key={r.ref} className="clickable" data-best={r.best || undefined} onClick={() => nav(`/runs/${encodeURIComponent(r.ref)}`)}>
                  <td><div className="row" style={{ gap: 6, fontWeight: 500 }}>{r.best && <Trophy size={13} color="var(--good)" aria-label="best so far" />}<span className="ellipsis" style={{ maxWidth: 260 }}>{r.name}</span></div><div className="faint" style={{ fontSize: 12 }}>{fmtAgo(r.created_at)}{r.user ? ` · ${r.user}` : ""}{r.branch ? ` · ${r.branch}` : ""}</div></td>
                  <td><StatusPill status={r.status} /></td>
                  {p.metrics.map((m) => <td key={m.key} className="r num">{fmtNum(r.values[m.key])}<Delta c={r.vs_previous} k={m.key} /></td>)}
                  <td><VsPill c={r.vs_previous} /></td>
                  <td>{r.best ? <span className="pill good">best</span> : <VsPill c={r.vs_best} />}</td>
                  <td className="r num muted">{fmtSecs(r.run_s)}</td>
                  <td className="r num faint">{fmtSecs(r.queue_s)}</td>
                  <td style={{ maxWidth: 220, minWidth: 160 }} onClick={(e) => e.stopPropagation()}><Changes c={r.changes ?? []} logs={r.log_problems ?? []} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
