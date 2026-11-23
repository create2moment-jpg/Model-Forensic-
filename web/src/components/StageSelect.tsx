import { Layers } from "lucide-react";

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
