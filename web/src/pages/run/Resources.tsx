/** Timeline → Resources: how the run used its machines — GPU / CPU utilisation, GPU / CPU memory, disk, network, per
 *  node and GPU — and what that says (idle GPUs, a CPU-bound input pipeline, memory headroom, a lagging node, a full
 *  disk). Azure Monitor when the role allows it, else what the run logged itself; said plainly which. */
import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, Cpu, Info, KeyRound } from "lucide-react";
import { api } from "../../api/client";
import type { ResourceSeriesDto, RunResources } from "../../api/types";
import { Chart, PALETTE, type ChartSeries } from "../../components/Chart";
import { Card, Empty, ErrorBox, Loading } from "../../components/ui";

const ORDER = ["gpu_util", "gpu_mem", "cpu_util", "cpu_mem", "disk_used", "net_in_mb", "net_out_mb"];
const ICON = { problem: <AlertOctagon size={15} color="var(--bad)" />, attention: <AlertTriangle size={15} color="var(--warn)" />, info: <Info size={15} color="var(--info)" /> };

function chartFor(series: ResourceSeriesDto[], t0: number): ChartSeries[] {
  const nodes = [...new Set(series.map((s) => s.node))];
  return series.map((s, i) => ({
    name: `${s.node}${s.device !== null ? ` · GPU ${s.device}` : ""}`,
    x: s.t.map((t) => (t - t0) / 3600),
    y: s.v,
    color: PALETTE[(nodes.length > 1 ? nodes.indexOf(s.node) : i) % PALETTE.length],
    width: 1.4,
  }));
}

export function ResourcesTab({ runId, stages, onStage }: { runId: string; stages?: string[]; onStage?: (s: string) => void }) {
  const q = useQuery({ queryKey: ["resources", runId], queryFn: () => api.resources(runId), staleTime: 60_000,
    refetchInterval: (x) => (x.state.data?.status === "running" ? 60_000 : false) });
  if (stages?.length && onStage) {
    return (
      <Card><Empty icon={<Cpu size={24} />} title="Pick a stage">Each stage of a pipeline ran on its own machines.
        <div className="row wrap" style={{ gap: 6, justifyContent: "center", marginTop: 10 }}>{stages.map((s) => <button key={s} className="btn sm" onClick={() => onStage(s)}>{s}</button>)}</div>
      </Empty></Card>
    );
  }
  if (q.isLoading) return <Loading label="Reading the machine metrics…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r: RunResources = q.data!;
  const access = r.access && (
    <div className="note row" style={{ gap: 8, marginBottom: 12 }} data-testid="resources-access"><KeyRound size={15} /><span>{r.access}</span></div>
  );
  if (r.state === "not_started") return <Card><Empty icon={<Cpu size={24} />} title="Not started yet">The machine metrics start when the run does.</Empty></Card>;
  if (r.state === "none") {
    return <>{access}<Card><Empty icon={<Cpu size={24} />} title="No machine metrics for this run">
      {r.access ? "Once the role is assigned they appear here." : "Neither the backend nor the run itself recorded GPU / CPU use. On Azure ML they come from Azure Monitor; a training script can also log them (MLflow system metrics)."}
    </Empty></Card></>;
  }
  const t0 = r.started_at ? new Date(r.started_at).getTime() / 1000 : Math.min(...r.series.flatMap((s) => s.t.slice(0, 1)));
  const kinds = ORDER.filter((k) => r.series.some((s) => s.kind === k));
  const tile = (k: string, what: "mean" | "peak", label: string) => {
    const v = r.summary[k]?.[what];
    return v === null || v === undefined ? null : <div key={`${k}${what}`}><b>{v.toFixed(0)}%</b><span>{label}</span></div>;
  };
  return (
    <div className="col" style={{ gap: 14 }} data-testid="resources">
      {access}
      <div className="row faint" style={{ fontSize: 12.5, gap: 6 }}>
        <Cpu size={13} /> From {r.source === "azure monitor" ? "Azure Monitor" : r.source === "logged by the run" ? "the system metrics the run logged" : "the run folder's system_metrics.jsonl"}
        {r.nodes && r.nodes.length > 0 && <> · {r.nodes.length} node{r.nodes.length === 1 ? "" : "s"}</>}
        {r.notes.map((n) => <span key={n}> · {n}</span>)}
      </div>
      {r.findings.length > 0 ? (
        <div className="col" style={{ gap: 8 }} data-testid="resources-findings">
          {r.findings.map((f) => (
            <div key={f.title} className={`resource-finding ${f.level}`}>
              {ICON[f.level]}<div><b>{f.title}</b><div className="muted" style={{ fontSize: 13 }}>{f.detail}</div></div>
            </div>
          ))}
        </div>
      ) : <div className="note" data-testid="resources-findings"><Info size={14} /> The machines were used well: GPUs busy, memory within bounds, nodes in step.</div>}
      <div className="track-stats">
        {tile("gpu_util", "mean", "GPU busy (mean)")}{tile("gpu_mem", "peak", "GPU memory (peak)")}{tile("cpu_util", "mean", "CPU busy (mean)")}{tile("cpu_mem", "peak", "CPU memory (peak)")}{tile("disk_used", "peak", "disk used (peak)")}
      </div>
      <div className="grid g2">
        {kinds.map((k) => (
          <Card key={k} title={r.summary[k]?.label ?? k} sub="hours since start" flush>
            <div style={{ padding: "4px 8px 8px" }}><Chart series={chartFor(r.series.filter((s) => s.kind === k), t0)} height={200} xLabel="hours" smoothing={0.8} showRaw={false} /></div>
          </Card>
        ))}
      </div>
    </div>
  );
}
