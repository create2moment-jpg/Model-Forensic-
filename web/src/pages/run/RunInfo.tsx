import { useQuery } from "@tanstack/react-query";
import { Fragment, useState, type ReactNode } from "react";
import { AlertTriangle, Cpu, Database, Info, RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import type { ComputeInfo, DatasetInfo, SplitFact } from "../../api/types";
import { Card, Empty, ErrorBox, Loading, Pill } from "../../components/ui";

const stageLabel = (s: string): string => s || "this run";

function Sources({ files }: { files: { stage: string; path: string; scanned: boolean; error?: string; size_bytes: number | null }[] }) {
  if (!files.length) return null;
  return (
    <details className="faint" style={{ fontSize: 12 }}>
      <summary>{files.filter((f) => f.scanned).length} of {files.length} log file(s) scanned</summary>
      <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
        {files.map((f) => <li key={`${f.stage}/${f.path}`} className="mono">{f.stage ? `${f.stage}/` : ""}{f.path}{f.error ? ` — ${f.error}` : ""}</li>)}
      </ul>
    </details>
  );
}

function SplitTable({ title, facts, showUrl }: { title: string; facts: SplitFact[]; showUrl?: boolean }) {
  if (!facts.length) return null;
  return (
    <div>
      <div className="sub-h">{title}</div>
      <div className="table-wrap"><table className="t compact">
        <thead><tr><th>Split</th><th>Version</th>{showUrl && <th>Dataset</th>}{showUrl && <th>Index</th>}<th>Source</th></tr></thead>
        <tbody>{facts.map((f) => (
          <tr key={`${f.split}-${f.version}`}>
            <td className="mono">{f.split}</td><td className="mono"><b>{f.version}</b></td>
            {showUrl && <td className="mono">{f.dataset_name}</td>}
            {showUrl && <td className="mono faint ellipsis" style={{ maxWidth: 360 }} title={f.index_url}>{f.index_url || "—"}</td>}
            <td className="mono faint ellipsis" style={{ maxWidth: 260 }} title={f.source}>{f.source}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </div>
  );
}

/** Which dataset (and split versions) each stage asked for in its config vs what its driver log shows it loaded. */
export function DatasetTab({ runId, stage = "" }: { runId: string; stage?: string }) {
  const [all, setAll] = useState(false);
  const q = useQuery({ queryKey: ["run-dataset", runId], queryFn: () => api.runDataset(runId) });
  const refresh = useQuery({ queryKey: ["run-dataset-refresh", runId], queryFn: () => api.runDataset(runId, true), enabled: false });
  const d: DatasetInfo | undefined = refresh.data ?? q.data;
  if (q.isLoading) return <Loading label="Reading configs and scanning the logs…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  if (!d) return null;
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row" style={{ gap: 8 }}>
        <span className="faint">Requested = composed config (<code>loomy_data_config</code>) · Loaded = driver-log lines. A mismatch silently invalidates a comparison.</span>
        <span className="spacer" />
        <button className="btn sm" onClick={() => void refresh.refetch()}><RefreshCw size={13} className={refresh.isFetching ? "spin" : ""} /> Re-scan</button>
      </div>
      {d.notes.map((n) => <div key={n} className="note"><Info size={14} />{n}</div>)}
      {stagesOf(d.stages, stage, all, setAll).length === 0 ? <Card><Empty icon={<Database size={26} />} title="No dataset information">Download the <b>Configs</b> and <b>Logs</b> presets on the Artifacts tab, then re-scan.</Empty></Card> : <>{stageOnly(d.stages, stage, all, setAll)}{stagesOf(d.stages, stage, all, setAll).map((s) => (
        <Card key={s.stage} title={<span className="row" style={{ gap: 6 }}><Database size={15} /> {stageLabel(s.stage)}</span>}
          right={s.mismatches.length ? <Pill tone="bad">requested ≠ loaded</Pill> : s.loaded.length ? <Pill tone="good">loaded as requested</Pill> : undefined}>
          <div className="col" style={{ gap: 10 }}>
            {s.mismatches.map((m) => <div key={m} className="error-box"><AlertTriangle size={15} color="var(--bad)" /><div>{m}</div></div>)}
            <div className="kv">
              {s.dataset_name && <><span>Dataset</span><span className="mono">{s.dataset_name}</span></>}
              {s.catalog_basepath && <><span>Catalog</span><span className="mono ellipsis" title={s.catalog_basepath}>{s.catalog_basepath}</span></>}
              {s.predict_usage && <><span>Predict usage</span><span className="mono">{s.predict_usage}</span></>}
              {s.dataset_split && <><span>Pinned split</span><span className="mono">{s.dataset_split}</span></>}
              {s.infer_latest_version && <><span>Infer latest version</span><span className="mono">{s.infer_latest_version}</span></>}
              <><span>Local dataset</span><span className="mono">{s.use_local_dataset ? `used: ${s.local_name} (${s.local_basepath})` : s.local_name ? `configured, not used (${s.local_name})` : "—"}</span></>
            </div>
            <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1.4fr)" }}>
              <SplitTable title="Requested (config)" facts={s.configured} />
              <SplitTable title="Loaded (driver log)" facts={s.loaded} showUrl />
            </div>
            {s.use_local_dataset && <SplitTable title="Local dataset versions" facts={s.local_configured} />}
            {s.notes.map((n) => <div key={n} className="faint">ⓘ {n}</div>)}
          </div>
        </Card>
      ))}</>}
      <Sources files={d.log_files} />
    </div>
  );
}

/** GPUs actually used (driver log), requested parallelism (config) and the job's compute target, per stage. */
export function ComputeTab({ runId, stage = "" }: { runId: string; stage?: string }) {
  const [all, setAll] = useState(false);
  const q = useQuery({ queryKey: ["run-compute", runId], queryFn: () => api.runCompute(runId) });
  if (q.isLoading) return <Loading label="Scanning the driver logs…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d: ComputeInfo | undefined = q.data;
  if (!d) return null;
  return (
    <div className="col" style={{ gap: 12 }}>
      {d.notes.map((n) => <div key={n} className="note"><Info size={14} />{n}</div>)}
      {stagesOf(d.stages, stage, all, setAll).length === 0 ? <Card><Empty icon={<Cpu size={26} />} title="No compute information">Download the <b>Logs</b> and <b>Configs</b> presets on the Artifacts tab.</Empty></Card> : <>{stageOnly(d.stages, stage, all, setAll)}{stagesOf(d.stages, stage, all, setAll).map((s) => {
        const byName = s.gpus.reduce<Record<string, number>>((a, g) => ({ ...a, [`${g.name} · ${g.memory_gib} GiB`]: (a[`${g.name} · ${g.memory_gib} GiB`] ?? 0) + 1 }), {});
        return (
          <Card key={s.stage} title={<span className="row" style={{ gap: 6 }}><Cpu size={15} /> {stageLabel(s.stage)}</span>}
            right={s.cpu_only ? <Pill tone="warn">CPU only</Pill> : s.gpus.length ? <Pill tone="good">{s.gpus.length} GPU{s.gpus.length > 1 ? "s" : ""}{s.gpu_log_files > 1 ? ` · ${s.gpu_log_files} nodes` : ""}</Pill> : undefined}>
            <div className="col" style={{ gap: 10 }}>
              {Object.keys(s.platform).length > 0 && (
                <div className="kv">{Object.entries(s.platform).map(([k, v]) => <Fragment key={k}><span>{k.replace(/_/g, " ")}</span><span className="mono">{String(v)}</span></Fragment>)}</div>
              )}
              {s.gpus.length > 0 && (
                <div>
                  <div className="sub-h">GPUs (driver log) — {Object.entries(byName).map(([n, c]) => `${c}× ${n}`).join(", ")}</div>
                  <div className="table-wrap" style={{ maxHeight: 260 }}>
                    <table className="t compact">
                      <thead><tr><th>Name</th><th className="r">Memory</th><th>Driver</th><th>VBIOS</th><th>Bus id</th><th>PCIe gen</th><th>Log file</th></tr></thead>
                      <tbody>{s.gpus.map((g, i) => (
                        <tr key={i}><td>{g.name}</td><td className="r num">{g.memory_gib} GiB</td><td className="mono">{g.driver_version || "—"}</td><td className="mono">{g.vbios_version || "—"}</td>
                          <td className="mono">{g.bus_id || "—"}</td><td className="mono">{g.pci_link_gen || "—"}</td><td className="mono faint ellipsis" style={{ maxWidth: 220 }} title={g.source}>{g.source}</td></tr>
                      ))}</tbody>
                    </table>
                  </div>
                </div>
              )}
              {s.parallelism.length > 0 && (
                <div>
                  <div className="sub-h">Requested parallelism (config)</div>
                  <div className="kv">{s.parallelism.map((p) => <Fragment key={p.key}><span>{p.key}</span><span className="mono" title={p.source}>{p.value}</span></Fragment>)}</div>
                </div>
              )}
              {s.notes.map((n) => <div key={n} className="faint">ⓘ {n}</div>)}
            </div>
          </Card>
        );
      })}</>}
      <Sources files={d.log_files} />
    </div>
  );
}

/** The page's stage only (when this run has it), unless "all stages" was clicked. */
function stagesOf<T extends { stage: string }>(rows: T[], stage: string, all: boolean, _set: (v: boolean) => void): T[] {
  return stage && !all && rows.some((r) => r.stage === stage) ? rows.filter((r) => r.stage === stage) : rows;
}

function stageOnly(rows: { stage: string }[], stage: string, all: boolean, set: (v: boolean) => void): ReactNode {
  if (!stage || !rows.some((r) => r.stage === stage) || rows.length < 2) return null;
  return (
    <div className="note" data-testid="stage-only-info">
      {all ? <>All stages shown.</> : <>Showing the stage <b>{stage}</b>.</>}
      <span className="spacer" />
      <button className="btn ghost sm" onClick={() => set(!all)}>{all ? `only ${stage}` : `all ${rows.length} stages`}</button>
    </div>
  );
}
