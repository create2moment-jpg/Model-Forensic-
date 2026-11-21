import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Download, HardDriveDownload, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../../api/client";
import type { Artifact } from "../../api/types";
import { useApp } from "../../components/AppState";
import { Card, Pill } from "../../components/ui";
import { fmtBytes } from "../../lib/format";

const PROFILES: { id: string; label: string; kinds: string[] | null; maxFile: number | null; help: string }[] = [
  { id: "fast_debug", label: "Fast debug", kinds: ["log", "metrics", "config", "evaluation", "environment"], maxFile: 50 * 1024 ** 2, help: "Logs, metrics, configs, small eval files" },
  { id: "evaluation_only", label: "Evaluation", kinds: ["evaluation", "predictions", "metrics"], maxFile: 2 * 1024 ** 3, help: "Evaluation results and predictions" },
  { id: "checkpoint_only", label: "Checkpoints", kinds: ["checkpoint", "model"], maxFile: null, help: "Model weights only" },
  { id: "full_forensic", label: "Full forensic", kinds: null, maxFile: null, help: "Everything" },
];
const KIND_TONE: Record<string, string> = { log: "outline", metrics: "info", config: "accent", checkpoint: "warn", evaluation: "good", predictions: "good", model: "warn", environment: "outline", other: "outline" };

export function ArtifactsTab({ runId, artifacts }: { runId: string; artifacts: Artifact[] }) {
  const { trackJob, toast, jobs } = useApp();
  const [filter, setFilter] = useState("");
  const [kind, setKind] = useState("all");
  const [sel, setSel] = useState<string[]>([]);
  const jobsQ = useQuery({ queryKey: ["jobs"], queryFn: api.jobs, enabled: false });
  void jobsQ;
  const downloaded = useMemo(() => new Set(jobs.filter((j) => j.status === "done").flatMap((j) => j.result?.files?.map((f) => f.path) ?? [])), [jobs]);
  const kinds = [...new Set(artifacts.map((a) => a.kind))].sort();
  const rows = artifacts.filter((a) => (kind === "all" || a.kind === kind) && a.path.toLowerCase().includes(filter.toLowerCase()));
  const total = artifacts.reduce((s, a) => s + (a.size_bytes ?? 0), 0);
  const profileSize = (p: (typeof PROFILES)[number]): [number, number] => {
    const ok = artifacts.filter((a) => (!p.kinds || p.kinds.includes(a.kind)) && (!p.maxFile || (a.size_bytes ?? 0) <= p.maxFile));
    return [ok.length, ok.reduce((s, a) => s + (a.size_bytes ?? 0), 0)];
  };
  const start = (profile: string, paths?: string[]): void => {
    void api.download(runId, profile, paths).then((j) => { trackJob(j.job_id); toast("Download started — see Jobs"); });
  };

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="grid g4">
        {PROFILES.map((p) => {
          const [n, size] = profileSize(p);
          return (
            <div key={p.id} className="card" style={{ padding: 14 }}>
              <div className="row"><HardDriveDownload size={15} color="var(--accent-2)" /><b>{p.label}</b><span className="spacer" /><span className="faint num">{n} files · {fmtBytes(size)}</span></div>
              <div className="faint" style={{ fontSize: 12, margin: "6px 0 10px" }}>{p.help}</div>
              <button className="btn sm" style={{ width: "100%", justifyContent: "center" }} disabled={!n} onClick={() => start(p.id)}>Download to cache</button>
            </div>
          );
        })}
      </div>
      <Card title="Artifacts" sub={`${artifacts.length} files · ${fmtBytes(total)}`} flush right={<>
        <div className="input-wrap" style={{ width: 240 }}><Search size={13} /><input className="input" placeholder="Filter path" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}><option value="all">All kinds</option>{kinds.map((k) => <option key={k}>{k}</option>)}</select>
        <button className="btn sm primary" disabled={!sel.length} onClick={() => { start("custom", sel); setSel([]); }}><Download size={13} /> Cache {sel.length || ""} selected</button>
      </>}>
        <div className="table-wrap" style={{ maxHeight: "calc(100vh - 420px)" }}>
          <table className="t">
            <thead><tr><th style={{ width: 30 }} /><th>Path</th><th>Kind</th><th className="r">Size</th><th>Cache</th><th className="r">Browser</th></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.path} className={sel.includes(a.path) ? "selected" : ""}>
                  <td><input type="checkbox" style={{ accentColor: "var(--accent)" }} checked={sel.includes(a.path)} onChange={() => setSel((s) => (s.includes(a.path) ? s.filter((x) => x !== a.path) : [...s, a.path]))} /></td>
                  <td className="mono">{a.path}</td>
                  <td><Pill tone={KIND_TONE[a.kind] ?? "outline"}>{a.kind}</Pill></td>
                  <td className="r num muted">{fmtBytes(a.size_bytes)}</td>
                  <td>{downloaded.has(a.path) ? <span className="row" style={{ color: "var(--good)", gap: 4 }}><CheckCircle2 size={13} /> cached</span> : <span className="faint">—</span>}</td>
                  <td className="r"><a className="btn ghost sm" href={api.artifactUrl(runId, a.path)} download><Download size={13} /> Save</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
