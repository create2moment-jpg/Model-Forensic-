import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, AlertTriangle, ArrowRight, CircleStop, CloudDownload, HeartPulse, Pause, Play, RotateCcw, Stethoscope, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { PrefetchItem, PrefetchPhase, PrefetchState, PrefetchSummary, PrefetchTask } from "../api/types";
import { Card, Empty, ErrorBox, Loading, Pill } from "../components/ui";
import { fmtAgo, fmtBytes } from "../lib/format";

const STATE: Record<PrefetchState, { tone: string; label: string; help: string }> = {
  planned: { tone: "info", label: "planned", help: "Nothing downloaded yet — review the files and sizes, then start" },
  running: { tone: "accent", label: "running", help: "Downloading the selection" },
  stopping: { tone: "warn", label: "stopping", help: "Finishing the current chunk, keeping everything done so far" },
  stopped: { tone: "warn", label: "stopped · resumable", help: "Finished files and partial downloads are kept — resume to continue" },
  completed: { tone: "good", label: "completed", help: "Every selected file is on disk and verified" },
  failed: { tone: "bad", label: "failed · resumable", help: "Some files failed — resume retries only those" },
  cancelled: { tone: "outline", label: "cancelled", help: "Not resumable — partial downloads were removed; finished files stay cached" },
};
const ITEM_TONE: Record<PrefetchItem["state"], string> = { pending: "outline", queued: "outline", downloading: "accent", validating: "accent", partial: "warn", reused: "info", done: "good", failed: "bad", cancelled: "outline" };
const MAIN_PHASES: PrefetchPhase[] = ["manifesting", "awaiting_selection", "queued", "downloading", "validating", "completed"];
const PHASE_LABEL: Record<string, string> = { planned: "planned", manifesting: "manifest", awaiting_selection: "awaiting selection", queued: "queued", downloading: "downloading", validating: "validating", completed: "completed", stopping: "stopping", stopped: "stopped · resumable", cancelled: "cancelled", failed: "failed" };
const SEV_TONE: Record<string, string> = { critical: "bad", warning: "warn", info: "info" };

/** Progress the evidence supports: a bar only when every size is known; otherwise an indeterminate bar and counts. */
function Bar({ t, big }: { t: PrefetchSummary; big?: boolean }) {
  const busy = t.state === "running" || t.state === "stopping";
  const p = t.progress;
  const frac = p ? p.fraction : progressOf(t);
  if (frac === null) return <div className={`pf-bar ${big ? "big" : ""} ${busy ? "indeterminate" : ""}`} title="Some sizes are unknown — no percentage is shown" data-testid="pf-indeterminate"><span style={{ width: busy ? "30%" : "0%" }} /></div>;
  return <div className={`pf-bar ${big ? "big" : ""}`} title={`${Math.round(frac * 100)} %`}><span style={{ width: `${frac * 100}%` }} /></div>;
}

/** Where the task is in its life: planned → manifest → awaiting selection → queued → downloading → validating → completed. */
function PhaseSteps({ t }: { t: PrefetchSummary }) {
  const ph = t.phase ?? (t.state === "planned" ? "awaiting_selection" : t.state);
  const at = MAIN_PHASES.indexOf(ph as PrefetchPhase);
  const off = at < 0;
  return (
    <div className="pf-phases" data-testid="pf-phases" data-phase={ph}>
      {MAIN_PHASES.map((p, i) => (
        <span key={p} className={`pf-phase ${!off && i < at ? "past" : ""} ${p === ph ? "now" : ""}`} title={t.phase_times?.[p] !== undefined ? `${t.phase_times[p]!.toFixed(1)} s` : undefined}>
          {PHASE_LABEL[p]}{i < MAIN_PHASES.length - 1 && <ArrowRight size={10} />}
        </span>
      ))}
      {off && <Pill tone={ph === "failed" ? "bad" : ph === "cancelled" ? "outline" : "warn"}>{PHASE_LABEL[ph] ?? ph}</Pill>}
    </div>
  );
}

const fmtSecs = (s: number | null): string => (s === null ? "—" : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);

/** Artifact prefetch tasks: plan (what, how big, what is reused, how long, risks) → start → stop / resume. Downloads
 *  run on the server: switching pages or tabs never pauses them. */
export function PrefetchPage() {
  const [sp, setSp] = useSearchParams();
  const tasks = useQuery({
    queryKey: ["prefetch-tasks"], queryFn: api.prefetchTasks,
    refetchInterval: (q) => ((q.state.data?.tasks ?? []).some((t) => t.state === "running" || t.state === "stopping") ? 1000 : 10_000),
  });
  const list = tasks.data?.tasks ?? [];
  const sel = sp.get("task") ?? list[0]?.id ?? null;
  return (
    <div className="page" style={{ maxWidth: 1400 }}>
      <div className="page-head"><div><h1 className="row"><CloudDownload size={20} /> Prefetch</h1>
        <div className="sub">Artifact downloads as tasks: plan first (files, sizes, what is reused, time, disk space, risks), then start; stop any time and resume later — after a restart too. They keep running while you work elsewhere.</div></div></div>
      {tasks.isLoading ? <Loading /> : tasks.error ? <ErrorBox error={tasks.error} /> : list.length === 0 ? (
        <Card><Empty icon={<CloudDownload size={28} />} title="No prefetch tasks yet">Plan one from a run's <b>Artifacts</b> tab, the <b>Bundle</b> button on a run, or select runs on the Runs page → <b>Plan prefetch</b>.</Empty></Card>
      ) : (
        <div className="prefetch-layout">
          <div className="col" style={{ gap: 6, minWidth: 0 }}>
            {list.map((t) => <TaskRow key={t.id} t={t} on={t.id === sel} onClick={() => setSp({ task: t.id })} />)}
          </div>
          <div style={{ minWidth: 0 }}>{sel && <TaskDetail id={sel} />}</div>
        </div>
      )}
    </div>
  );
}

function progressOf(t: PrefetchSummary): number {
  const total = t.bytes_selected || 1;
  return Math.min(1, (total - t.bytes_remaining) / total);
}

function TaskRow({ t, on, onClick }: { t: PrefetchSummary; on: boolean; onClick: () => void }) {
  const s = STATE[t.state];
  return (
    <button className={`card prefetch-row ${on ? "on" : ""}`} onClick={onClick} data-state={t.state}>
      <div className="row" style={{ gap: 6 }}><b className="ellipsis" style={{ flex: 1, textAlign: "left" }}>{t.title}</b><Pill tone={s.tone}>{s.label}</Pill></div>
      <Bar t={t} />
      <div className="row faint" style={{ fontSize: 12, gap: 8 }}>
        {t.phase && <span>{PHASE_LABEL[t.phase] ?? t.phase}</span>}
        {t.stalled && <span className="bad">stalled</span>}
        <span>{t.files_selected - t.files_remaining}/{t.files_selected} files ready</span>
        <span>{fmtBytes(t.bytes_selected - t.bytes_remaining)} / {fmtBytes(t.bytes_selected)}</span>
        {t.state === "running" && t.throughput_bps > 0 && <span>{fmtBytes(t.throughput_bps)}/s · {fmtSecs(t.eta_s)} left</span>}
        <span className="spacer" />{fmtAgo(t.updated_at)}
      </div>
    </button>
  );
}

function TaskDetail({ id }: { id: string }) {
  const qc = useQueryClient();
  const [, setSp] = useSearchParams();
  const [filter, setFilter] = useState<"selected" | "all" | "todo" | "failed">("selected");
  // Pending ticks, applied in the same click (a controlled checkbox would otherwise flip back until the server answers).
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const q = useQuery({
    queryKey: ["prefetch-task", id], queryFn: () => api.prefetchTask(id),
    refetchInterval: (qq) => (qq.state.data && (qq.state.data.state === "running" || qq.state.data.state === "stopping") ? 1000 : false),
  });
  const refresh = (t?: PrefetchTask): void => {
    if (t) qc.setQueryData(["prefetch-task", id], t);
    void qc.invalidateQueries({ queryKey: ["prefetch-tasks"] });
    void qc.invalidateQueries({ queryKey: ["prefetch-task", id] });
  };
  const act = useMutation({ mutationFn: (a: "start" | "stop" | "cancel") => api.prefetchAction(id, a), onSuccess: refresh });
  const selm = useMutation({
    mutationFn: (b: { only?: string[]; keys?: string[]; selected?: boolean }) => api.prefetchSelect(id, b),
    onMutate: (b) => {  // tick immediately; the server's answer replaces it (or the error rolls it back)
      const prev = qc.getQueryData<PrefetchTask>(["prefetch-task", id]);
      if (prev) {
        const only = b.only ? new Set(b.only) : null;
        const keys = new Set(b.keys ?? []);
        qc.setQueryData<PrefetchTask>(["prefetch-task", id], {
          ...prev,
          items: prev.items.map((i) => (only ? { ...i, selected: only.has(i.key) } : keys.has(i.key) ? { ...i, selected: !!b.selected } : i)),
        });
      }
      return { prev };
    },
    onError: (_e, _b, ctx) => { if (ctx?.prev) qc.setQueryData(["prefetch-task", id], ctx.prev); },
    onSuccess: (t) => { refresh(t); setPending({}); },
    onSettled: () => setPending({}),
  });
  const del = useMutation({ mutationFn: () => api.prefetchDelete(id), onSuccess: () => { setSp({}); void qc.invalidateQueries({ queryKey: ["prefetch-tasks"] }); } });
  const t = q.data;
  const items = useMemo(() => (t?.items ?? []).filter((i) =>
    filter === "all" ? true : filter === "selected" ? i.selected : filter === "failed" ? i.state === "failed" : i.selected && i.state !== "done" && i.state !== "reused"), [t, filter]);
  if (q.isLoading) return <Loading />;
  if (q.error || !t) return <ErrorBox error={q.error} />;
  const s = STATE[t.state];
  const editable = t.state === "planned" || t.state === "stopped" || t.state === "failed";
  const busy = t.state === "running" || t.state === "stopping";
  return (
    <div className="col" style={{ gap: 12 }}>
      <Card title={<span className="row" style={{ gap: 8 }}>{t.title} <Pill tone={s.tone}>{s.label}</Pill></span>}
        sub={<span>{s.help} · <Link to={`/runs/${encodeURIComponent(t.run)}?tab=artifacts`}>{t.run}</Link></span>}
        right={<div className="row" style={{ gap: 6 }}>
          {t.state === "planned" && <button className="btn primary sm" disabled={act.isPending || t.files_selected === 0} onClick={() => act.mutate("start")}><Play size={13} /> Start</button>}
          {(t.state === "stopped" || t.state === "failed") && t.resumable && <button className="btn primary sm" disabled={act.isPending} onClick={() => act.mutate("start")}><RotateCcw size={13} /> Resume</button>}
          {busy && <button className="btn sm" disabled={t.state === "stopping"} onClick={() => act.mutate("stop")}><Pause size={13} /> Stop · resume later</button>}
          {(busy || editable) && <button className="btn sm ghost" onClick={() => { if (confirm("Cancel this task? Partial downloads are removed and it cannot be resumed (finished files stay in the cache).")) act.mutate("cancel"); }}><CircleStop size={13} /> Cancel</button>}
          {!busy && <button className="btn sm ghost icon" title="Delete the task (files stay in the cache)" onClick={() => del.mutate()}><Trash2 size={13} /></button>}
        </div>}>
        <div className="pf-stats">
          <Stat label="selected" value={`${t.files_selected} of ${t.files_total} files`} sub={fmtBytes(t.bytes_selected) + (t.unknown_sizes ? ` + ${t.unknown_sizes} of unknown size` : "")} />
          <Stat label="reused (verified)" value={`${t.files_reused + t.files_done} files`} sub={fmtBytes(t.bytes_reused)} />
          <Stat label="left to fetch" value={`${t.files_remaining} files`} sub={`${fmtBytes(t.bytes_remaining)}${t.files_partial ? ` · ${t.files_partial} resume from a partial download` : ""}`} />
          <Stat label={busy ? "speed · time left" : "estimated time"} value={busy && t.throughput_bps ? `${fmtBytes(t.throughput_bps)}/s` : fmtSecs(t.eta_s)}
            sub={busy ? `${fmtSecs(t.eta_s)} left · ${t.workers} workers` : t.eta_from ? `from ${t.eta_from}` : "no download seen yet to estimate from"} />
          <Stat label="free disk" value={fmtBytes(t.free_bytes)} sub={t.elapsed_s ? `ran ${fmtSecs(t.elapsed_s)} so far` : ""} />
        </div>
        <PhaseSteps t={t} />
        <Bar t={t} big />
        <LiveStrip t={t} />
        {t.risks.map((r) => <div key={r} className="note warn" style={{ marginTop: 6 }}><AlertTriangle size={14} /> {r}</div>)}
        {(t.note || t.error) && <div className={`note ${t.error ? "warn" : ""}`} style={{ marginTop: 6 }}>{t.error || t.note}</div>}
      </Card>
      {!!t.diagnosis?.length && (
        <Card title={<span className="row"><Stethoscope size={15} /> Why it is slow or blocked</span>} sub="Only causes the recorded evidence supports">
          <div className="col" style={{ gap: 8 }} data-testid="pf-diagnosis">
            {t.diagnosis.map((d) => (
              <div key={d.cause} className={`note ${d.severity === "critical" ? "warn" : ""}`}>
                <div><Pill tone={SEV_TONE[d.severity] ?? "outline"}>{d.severity}</Pill> <b>{d.cause.replace(/_/g, " ")}</b> — {d.evidence}</div>
                <div className="faint" style={{ marginTop: 2 }}>Fix: {d.fix}</div>
              </div>
            ))}
          </div>
        </Card>
      )}
      <Card title="Files" sub={editable ? "Change the selection before starting (or while stopped)" : "The selection is fixed while the task runs"} right={
        <div className="row" style={{ gap: 6 }}>
          {(["selected", "todo", "failed", "all"] as const).map((f) => <button key={f} className={`filter-chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>{f === "todo" ? "left to fetch" : f}</button>)}
          {editable && <><button className="btn sm ghost" onClick={() => selm.mutate({ only: t.items.map((i) => i.key) })}>Select all</button>
            <button className="btn sm ghost" onClick={() => selm.mutate({ only: [] })}>None</button></>}
        </div>} flush>
        <div className="table-wrap" style={{ maxHeight: 520 }}>
          <table className="t">
            <thead><tr><th style={{ width: 28 }} /><th>File</th><th>Stage</th><th>Kind</th><th className="r">Size</th><th>State</th></tr></thead>
            <tbody>
              {items.slice(0, 2000).map((i) => (
                <tr key={i.key}>
                  <td><input type="checkbox" aria-label={`select ${i.stage ? `${i.stage}/` : ""}${i.path}`} checked={pending[i.key] ?? i.selected} disabled={!editable} onChange={(e) => { const v = e.target.checked; setPending((p) => ({ ...p, [i.key]: v })); selm.mutate({ keys: [i.key], selected: v }); }} /></td>
                  <td className="mono" style={{ fontSize: 12, wordBreak: "break-all", whiteSpace: "normal", maxWidth: 560 }}>{i.path}</td>
                  <td className="faint">{i.stage ?? "—"}</td>
                  <td className="faint">{i.kind}</td>
                  <td className="r num">{fmtBytes(i.size)}</td>
                  <td>
                    <Pill tone={ITEM_TONE[i.state] ?? "outline"}>{i.state === "reused" ? "reused · verified" : i.state === "partial" ? `partial · ${fmtBytes(i.partial_bytes)}` : i.state === "downloading" ? `downloading · ${fmtBytes(i.partial_bytes + (i.bytes_done ?? 0))}` : i.state}</Pill>
                    {(i.attempts ?? 0) > 1 && <span className="faint" style={{ fontSize: 11 }}> · attempt {i.attempts}</span>}
                    {i.error && <div className="bad" style={{ fontSize: 11 }}>{i.error}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.length > 2000 && <div className="faint" style={{ padding: 8 }}>… {items.length - 2000} more</div>}
          {items.length === 0 && <div className="muted" style={{ padding: 12 }}>No files in this view.</div>}
        </div>
      </Card>
      {t.events.length > 0 && (
        <Card title="Events" sub="Structured: phase · file · retry · stall · recovered · error">
          <div className="col" style={{ gap: 2, fontSize: 12.5, maxHeight: 320, overflow: "auto" }} data-testid="pf-events">{[...t.events].reverse().map((e, k) => (
            <div key={k} data-kind={e.kind ?? "info"}><span className="faint">{new Date(e.at).toLocaleTimeString()}</span> <Pill tone={e.kind === "error" || e.kind === "stall" ? "bad" : e.kind === "retry" ? "warn" : e.kind === "phase" ? "accent" : "outline"}>{e.kind ?? "info"}</Pill> {e.text}</div>
          ))}</div>
        </Card>
      )}
    </div>
  );
}

/** Is it running, what is it doing, what blocks it, what happens next — live. */
function LiveStrip({ t }: { t: PrefetchTask }) {
  const busy = t.state === "running" || t.state === "stopping";
  const counts = Object.entries(t.item_states ?? {}).filter(([, n]) => n > 0);
  return (
    <div className="col" style={{ gap: 6, marginTop: 6 }} data-testid="pf-live">
      <div className="row wrap" style={{ gap: 10, fontSize: 12.5 }}>
        {busy && (t.alive === false
          ? <span className="bad row" style={{ gap: 4 }}><HeartPulse size={13} /> no heartbeat for {t.heartbeat_age_s} s</span>
          : <span className="good-text row" style={{ gap: 4 }}><HeartPulse size={13} /> alive{t.heartbeat_age_s !== null && t.heartbeat_age_s !== undefined ? ` · ${t.heartbeat_age_s.toFixed(0)} s ago` : ""}</span>)}
        {t.phase && <span className="row" style={{ gap: 4 }}><Activity size={13} /> {PHASE_LABEL[t.phase] ?? t.phase}{t.phase_elapsed_s !== undefined && busy ? ` for ${fmtSecs(t.phase_elapsed_s)}` : ""}</span>}
        {t.progress && !t.progress.determinate && <span className="faint">{t.progress.files_done}/{t.progress.files_selected} files · {fmtBytes(t.progress.bytes_done)} so far (some sizes unknown — no percentage)</span>}
        {!!t.retries && <span className="warn-text">{t.retries} retr{t.retries === 1 ? "y" : "ies"}</span>}
        {counts.map(([k, n]) => <Pill key={k} tone={ITEM_TONE[k as PrefetchItem["state"]] ?? "outline"}>{n} {k}</Pill>)}
      </div>
      {t.stalled && <div className="note warn"><AlertTriangle size={14} /> Stalled — no byte arrived recently. See the diagnosis below.</div>}
      {!!t.in_flight?.length && (
        <div className="col faint" style={{ gap: 2, fontSize: 12 }}>
          {t.in_flight.slice(0, 8).map((f) => <div key={f.path} className="mono ellipsis">{f.state} {f.path} · {fmtBytes(f.bytes)}{f.size ? ` / ${fmtBytes(f.size)}` : ""} · {fmtSecs(f.for_s)}{f.attempts > 1 ? ` · attempt ${f.attempts}` : ""}</div>)}
        </div>
      )}
      {t.next && <div className="faint" style={{ fontSize: 12.5 }}><b>Next:</b> {t.next}</div>}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="pf-stat"><div className="faint" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4 }}>{label}</div><b>{value}</b>{sub && <div className="faint" style={{ fontSize: 12 }}>{sub}</div>}</div>;
}

/** Plan a prefetch and open it — used by the run page, the Artifacts tab and the command palette. */
export async function planAndOpen(nav: (to: string) => void, body: { run: string; profile?: string; stages?: string[]; paths?: string[] }): Promise<void> {
  const t = await api.prefetchPlan(body);
  nav(`/prefetch?task=${encodeURIComponent(t.id)}`);
}
