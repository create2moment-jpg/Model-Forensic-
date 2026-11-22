import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CircleStop, CloudDownload, Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { PrefetchItem, PrefetchState, PrefetchSummary, PrefetchTask } from "../api/types";
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
const ITEM_TONE: Record<PrefetchItem["state"], string> = { pending: "outline", partial: "warn", reused: "info", done: "good", failed: "bad" };

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
      <div className="pf-bar"><span style={{ width: `${progressOf(t) * 100}%` }} /></div>
      <div className="row faint" style={{ fontSize: 12, gap: 8 }}>
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
        <div className="pf-bar big"><span style={{ width: `${progressOf(t) * 100}%` }} /></div>
        {t.risks.map((r) => <div key={r} className="note warn" style={{ marginTop: 6 }}><AlertTriangle size={14} /> {r}</div>)}
        {(t.note || t.error) && <div className={`note ${t.error ? "warn" : ""}`} style={{ marginTop: 6 }}>{t.error || t.note}</div>}
      </Card>
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
                    <Pill tone={ITEM_TONE[i.state]}>{i.state === "reused" ? "reused · verified" : i.state === "partial" ? `partial · ${fmtBytes(i.partial_bytes)}` : i.state}</Pill>
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
        <Card title="History">
          <div className="col" style={{ gap: 2, fontSize: 12.5 }}>{[...t.events].reverse().map((e, k) => <div key={k}><span className="faint">{new Date(e.at).toLocaleString()}</span> — {e.text}</div>)}</div>
        </Card>
      )}
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
