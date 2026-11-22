import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Download, FileDown, HardDriveDownload, Loader2, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, refStr } from "../../api/client";
import type { Artifact } from "../../api/types";
import { useApp } from "../../components/AppState";
import { Card, Pill } from "../../components/ui";
import { fmtBytes } from "../../lib/format";

const KIND_TONE: Record<string, string> = { log: "outline", metrics: "info", config: "accent", checkpoint: "warn", evaluation: "good", predictions: "good", model: "warn", environment: "outline", other: "outline" };
// Profile order on the page: the v1 presets first, then the broader ones.
const ORDER = ["fast_debug", "logs", "configs", "eval_outputs", "eval_json", "eval_parquet", "metrics_csv", "evaluation_only", "checkpoint_only", "full_forensic"];
const BIG = 5 * 1024 ** 3;
const keyOf = (a: Artifact): string => `${refStr(a.run)}::${a.path}`;

export function ArtifactsTab({ runId, artifacts }: { runId: string; artifacts: Artifact[] }) {
  const { trackJob, toast, jobs } = useApp();
  const [filter, setFilter] = useState("");
  const [kind, setKind] = useState("all");
  const [stages, setStages] = useState<string[]>([]);
  const [sel, setSel] = useState<string[]>([]);
  const plans = useQuery({ queryKey: ["download-plans", runId, stages], queryFn: () => api.downloadPlans(runId, stages) });
  const exportM = useMutation({ mutationFn: () => api.exportMetrics(runId), onSuccess: (d) => toast(`Metric histories written to ${d.csv}`, "good") });
  const downloaded = useMemo(
    () => new Set(jobs.filter((j) => j.status === "done").flatMap((j) => j.result?.files?.map((f) => `${f.run ?? runId}::${f.path}`) ?? [])),
    [jobs, runId],
  );
  const stageNames = plans.data?.stages ?? [];
  const kinds = [...new Set(artifacts.map((a) => a.kind))].sort();
  const rows = artifacts.filter(
    (a) => (kind === "all" || a.kind === kind) && (!stages.length || stages.includes(a.stage ?? "")) && a.path.toLowerCase().includes(filter.toLowerCase()),
  );
  const total = rows.reduce((s, a) => s + (a.size_bytes ?? 0), 0);
  const start = (profile: string, bytes: number, paths?: string[]): void => {
    if (bytes > BIG && !window.confirm(`This downloads ${fmtBytes(bytes)}. Continue?`)) return;
    void api.download(runId, profile, paths, stages).then((j) => { trackJob(j.job_id); toast("Download started — see Jobs"); });
  };
  const toggleStage = (s: string): void => setStages((xs) => (xs.includes(s) ? xs.filter((x) => x !== s) : [...xs, s]));
  const profiles = ORDER.filter((id) => plans.data?.profiles[id]);
  const cat = useQuery({ queryKey: ["catalog", runId], queryFn: () => api.runCatalog(runId) });
  const doneJobs = jobs.filter((j) => j.status === "done").length;
  useEffect(() => { void cat.refetch(); void plans.refetch(); }, [doneJobs]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="col" style={{ gap: 14 }}>
      {stageNames.length > 0 && (
        <div className="row wrap" style={{ gap: 6 }}>
          <span className="faint">Stages</span>
          <button className={`btn sm ${stages.length === 0 ? "primary" : ""}`} onClick={() => setStages([])}>all</button>
          {stageNames.map((s) => <button key={s} className={`btn sm mono ${stages.includes(s) ? "primary" : ""}`} onClick={() => toggleStage(s)}>{s}</button>)}
        </div>
      )}
      {cat.data && cat.data.families.length > 0 && (
        <Card title="What's in this run" sub={`${cat.data.files} files · ${fmtBytes(cat.data.bytes)} — pick what to download by the question it answers`} flush>
          <div className="table-wrap"><table className="t compact">
            <thead><tr><th>Family</th><th>Answers</th><th className="r">Files</th><th className="r">Size</th><th>Cached</th><th /></tr></thead>
            <tbody>{cat.data.families.map((f) => (
              <tr key={f.family} title={`Worth fetching: ${f.worth}\nCaveat: ${f.caveat}`}>
                <td><b>{f.family}</b>{f.stages.length > 0 && <div className="faint" style={{ fontSize: 11 }}>{f.stages.join(", ")}</div>}</td>
                <td className="wrap" style={{ minWidth: 300 }}>{f.answers}<div className="faint" style={{ fontSize: 11.5 }}>{f.worth} · {f.caveat}</div></td>
                <td className="r num">{f.files}</td>
                <td className="r num">{fmtBytes(f.bytes)}{f.unsized ? <span className="faint"> +{f.unsized} unsized</span> : null}</td>
                <td>{f.cached === f.files ? <Pill tone="good">all</Pill> : f.cached ? <Pill tone="outline">{f.cached}/{f.files}</Pill> : <span className="faint">—</span>}</td>
                <td>{f.cached < f.files && <button className="btn sm" onClick={() => start("full_forensic", f.bytes, f.paths)}><Download size={13} /> Download</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        </Card>
      )}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10 }}>
        {plans.isLoading && <div className="faint"><Loader2 size={14} className="spin" /> sizing profiles…</div>}
        {profiles.map((id) => {
          const pl = plans.data?.profiles[id];
          if (!pl) return null;
          return (
            <div key={id} className="card" style={{ padding: 12 }}>
              <div className="row"><HardDriveDownload size={14} color="var(--accent-2)" /><b>{pl.label}</b></div>
              <div className="faint" style={{ fontSize: 12, margin: "4px 0 8px", minHeight: 30 }}>{pl.help}</div>
              <div className="row faint num" style={{ fontSize: 12, marginBottom: 8 }}>
                {pl.files} files · {fmtBytes(pl.bytes)}{pl.unknown_size ? ` + ${pl.unknown_size} unsized` : ""}
                <span className="spacer" />{pl.cached > 0 && <span style={{ color: "var(--good)" }}>{pl.cached} cached</span>}
              </div>
              <button className={`btn sm ${pl.bytes > BIG ? "danger" : ""}`} style={{ width: "100%", justifyContent: "center" }} disabled={!pl.files || pl.cached === pl.files} onClick={() => start(id, pl.bytes)}>
                {pl.cached === pl.files && pl.files ? "All cached" : "Download to cache"}
              </button>
            </div>
          );
        })}
        <div className="card" style={{ padding: 12 }}>
          <div className="row"><FileDown size={14} color="var(--accent-2)" /><b>Metric histories</b></div>
          <div className="faint" style={{ fontSize: 12, margin: "4px 0 8px", minHeight: 30 }}>All series (stages merged, raw names kept) as JSON + CSV</div>
          <div className="faint num" style={{ fontSize: 12, marginBottom: 8 }}>&nbsp;</div>
          <button className="btn sm" style={{ width: "100%", justifyContent: "center" }} disabled={exportM.isPending} onClick={() => exportM.mutate()}>{exportM.isPending ? <Loader2 size={13} className="spin" /> : null} Export</button>
        </div>
      </div>
      {plans.data && <div className="faint">Local copy: <span className="mono">{plans.data.local_root}</span> · every download is recorded in <span className="mono">download_manifest.json</span> (remote run, stage, path, sha256). Cached files are never fetched twice.</div>}
      <Card title="Files" sub={`${rows.length} files · ${fmtBytes(total)}`} flush right={<>
        <div className="input-wrap" style={{ width: 240 }}><Search size={13} /><input className="input" placeholder="Filter path" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}><option value="all">All kinds</option>{kinds.map((k) => <option key={k}>{k}</option>)}</select>
        <button className="btn sm primary" disabled={!sel.length} onClick={() => { start("custom", rows.filter((a) => sel.includes(keyOf(a))).reduce((s, a) => s + (a.size_bytes ?? 0), 0), sel); setSel([]); }}><Download size={13} /> Cache {sel.length || ""} selected</button>
      </>}>
        <div className="table-wrap" style={{ maxHeight: "calc(100vh - 420px)" }}>
          <table className="t">
            <thead><tr><th style={{ width: 30 }} />{stageNames.length > 0 && <th>Stage</th>}<th>Path</th><th>Kind</th><th className="r">Size</th><th>Cache</th><th className="r">Browser</th></tr></thead>
            <tbody>
              {rows.map((a) => {
                const k = keyOf(a);
                return (
                  <tr key={k} className={sel.includes(k) ? "selected" : ""}>
                    <td><input type="checkbox" style={{ accentColor: "var(--accent)" }} checked={sel.includes(k)} onChange={() => setSel((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]))} /></td>
                    {stageNames.length > 0 && <td className="mono faint">{a.stage ?? "pipeline"}</td>}
                    <td className="mono wrap" style={{ wordBreak: "break-all" }}>{a.path.replace(/^stage_output\//, "")}</td>
                    <td><Pill tone={KIND_TONE[a.kind] ?? "outline"}>{a.kind}</Pill></td>
                    <td className="r num muted">{fmtBytes(a.size_bytes)}</td>
                    <td>{downloaded.has(k) ? <span className="row" style={{ color: "var(--good)", gap: 4 }}><CheckCircle2 size={13} /> cached</span> : <span className="faint">—</span>}</td>
                    <td className="r"><a className="btn ghost sm" href={api.artifactUrl(runId, a.stage ? k : a.path)} download><Download size={13} /> Save</a></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
