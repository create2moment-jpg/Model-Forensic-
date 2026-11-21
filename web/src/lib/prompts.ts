import type { PromptTemplate } from "../api/types";

/** Client-side preview of the server's template renderer ({{var}}, {{#var}}…{{/var}}, {{^var}}…{{/var}}). */
export function renderPreview(template: string, vars: Record<string, string | null | undefined>): string {
  const sections = /\{\{([#^])([a-z_][a-z0-9_]*)\}\}([\s\S]*?)\{\{\/\2\}\}/g;
  let text = template.replace(sections, (_m, kind: string, name: string, body: string) => ((!!vars[name]) === (kind === "#") ? body : ""));
  text = text.replace(/\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}/g, (_m, name: string) => vars[name] || `‹${name}›`);
  return text.trim();
}

export const VARIABLE_HELP: Record<string, { label: string; placeholder: string }> = {
  change_description: { label: "What changed", placeholder: "ResNet-50 → ConvNeXt-B, radar FPN 1 → 3 levels" },
  backbone: { label: "Backbone", placeholder: "ConvNeXt-B (IN-22k)" },
  sensor: { label: "Sensor", placeholder: "camera / lidar / radar" },
  component: { label: "Component / swept parameter", placeholder: "radar FPN levels, optimizer.lr, decoder" },
  focus_metrics: { label: "Focus metrics", placeholder: "NDS, mATE, AMOTA" },
  slice_dimension: { label: "Slice dimension", placeholder: "range, class, weather" },
  budget_gpu_hours: { label: "Budget (GPU-hours)", placeholder: "200" },
  hypothesis: { label: "Hypothesis / claim", placeholder: "Long-range drop is caused by radar sparsity" },
  modalities: { label: "Modalities", placeholder: "camera, lidar, radar" },
  temporal_window: { label: "Temporal window", placeholder: "8 frames" },
  loss_terms: { label: "Loss terms", placeholder: "loss/cls, loss/bbox" },
  metric_name: { label: "Metric", placeholder: "val/NDS" },
  evaluation_split: { label: "Evaluation split", placeholder: "val" },
  dataset: { label: "Dataset", placeholder: "nuScenes v1.0" },
  model: { label: "Model", placeholder: "Sparse4D" },
  date_range: { label: "Date range", placeholder: "last 7 days" },
  notes: { label: "Notes for the model", placeholder: "Anything else it should know" },
};

export function evidenceSummary(p: PromptTemplate): string[] {
  const out: string[] = [];
  if (p.include.logs !== "none") out.push(`logs: ${p.include.logs}`);
  if (p.include.series.length) out.push(`curves: ${p.include.series.join(", ")}`);
  if (p.include.config_keys.length) out.push(`config: ${p.include.config_keys.length} key patterns`);
  if (p.include.slices) out.push("per-slice metrics");
  return out;
}

export const isMultiRun = (p: PromptTemplate): boolean => p.template.includes("{{run_ids}}") && !p.template.includes("{{#run_ids}}");
