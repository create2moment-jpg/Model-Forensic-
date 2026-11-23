import { useQuery } from "@tanstack/react-query";
import { Layers } from "lucide-react";
import { api } from "../api/client";

/** Which pipeline stage an analysis reads — never two stages mixed. "" = each metric from the last stage that logged it. */
export function StageSelect({ stages, value, onChange }: { stages: string[]; value: string; onChange: (v: string) => void }) {
  if (stages.length < 2) return null;
  return (
    <label className="row stage-select" style={{ gap: 4 }} title="The stage the analysis reads (pretrain, finetune and evaluation are never mixed)">
      <Layers size={12} className="faint" />
      <select className="input" aria-label="Stage to analyse" value={value} onChange={(e) => onChange(e.target.value)} style={{ height: 26, fontSize: 12, maxWidth: 200 }}>
        <option value="">final (each metric's last stage)</option>
        {stages.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
    </label>
  );
}

/** A pipeline stage is a run of its own: the run id of ``stage`` in ``runId`` (``runId`` itself without a stage, or
 *  while the stages load). Views that read one run (config, logs, findings, setup) read the stage's run. */
export function useStageRun(runId: string, stage: string): string {
  const q = useQuery({ queryKey: ["stages", runId], queryFn: () => api.stages(runId), enabled: !!stage && !!runId && !runId.startsWith("group:"), staleTime: 60_000 });
  return (stage && q.data?.find((s) => s.name === stage)?.ref) || runId;
}
