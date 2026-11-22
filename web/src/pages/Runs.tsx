import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Columns3, Download, GitCompare, Info, Microscope, Search, Star, TrendingDown, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, refStr } from "../api/client";
import type { Run } from "../api/types";
import { useApp } from "../components/AppState";
import { Empty, ErrorBox, Loading, StatusPill } from "../components/ui";
import { fmtAgo, fmtDuration, fmtNum, shortSha } from "../lib/format";
import { useLocalStorage } from "../lib/hooks";

const QUICK = [
  { label: "All", q: "" },
  { label: "Mine", q: "user:me" },
  { label: "Failed", q: "status:failed" },
  { label: "Running", q: "status:running" },
  { label: "Last 7 days", q: "since:7d" },
  { label: "Baselines", q: "tag:role=baseline" },
  { label: "Favorites", q: "is:fav" },
];
const EXAMPLES = ["failed training jobs from last week", "show runs using 8-frame temporal windows", "NDS>0.6 tag:role=baseline", "user:me status:failed", "user:sam branch:feat/radar-fusion"];

type SortKey = "name" | "status" | "experiment" | "user" | "created" | "duration" | `m:${string}`;

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
  const { trackJob, toast, workspace } = useApp();
  const res = useQuery({ queryKey: ["runs", "explorer", q, workspace], queryFn: () => api.search(q, 500, 0, workspace), placeholderData: keepPreviousData });
  const runs = res.data?.runs ?? [];

  const allMetrics = useMemo(() => {
    const c = new Map<string, number>();
    runs.forEach((r) => Object.keys(r.summary_metrics).forEach((k) => c.set(k, (c.get(k) ?? 0) + 1)));
    return [...c.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  }, [runs]);
  const cols = metricCols ?? allMetrics.filter((m) => ["NDS", "mAP", "AMOTA"].includes(m)).concat(allMetrics.length && !allMetrics.includes("NDS") ? allMetrics.slice(0, 3) : []);

  const sorted = useMemo(() => {
    const val = (r: Run): string | number => {
      const k = sort.key;
      if (k.startsWith("m:")) return r.summary_metrics[k.slice(2)] ?? -Infinity;
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
        <span className="spacer" />
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
          <button className="btn sm" onClick={() => { selected.forEach((id) => void api.download(id, "fast_debug").then((r) => trackJob(r.job_id))); }}><Download size={13} /> Fast-debug download</button>
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
                  {th("name", "Run")}{th("status", "Status")}{th("experiment", "Experiment")}{th("user", "User")}
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
                        </div>
                      </td>
                      <td className="wrap" style={{ maxWidth: 320, minWidth: 220 }}>
                        <div style={{ fontWeight: 550 }} className="ellipsis">{r.display_name ?? r.name}</div>
                        <div className="row wrap" style={{ gap: 4, marginTop: 2 }}>
                          {!workspace && <span className="tag" style={{ background: "var(--accent-bg)", color: "var(--accent-2)" }} title="Workspace">{r.ref.backend}</span>}
                          {Object.entries(r.tags).slice(0, 3).map(([k, v]) => <span key={k} className="tag">{k}={v}</span>)}
                        </div>
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
