// Mirrors backend/src/model_forensic/domain/*. Keep in sync with the pydantic models.
export type RunStatus = "queued" | "running" | "completed" | "failed" | "canceled" | "unknown";
export type Severity = "info" | "low" | "medium" | "high" | "critical";
export type Confidence = "low" | "medium" | "high";
export type Label = "observed" | "computed" | "inferred" | "hypothesized" | "recommended" | "ai_generated";

export interface RunRef { backend: string; run_id: string }
export interface Run {
  ref: RunRef;
  name: string;
  display_name: string | null;
  experiment: string | null;
  kind: string;
  status: RunStatus;
  created_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  user: string | null;
  description: string | null;
  tags: Record<string, string>;
  params: Record<string, string>;
  command: string | null;
  environment: string | null;
  git: { commit: string | null; branch: string | null; repository: string | null; dirty: boolean | null };
  compute: { target: string | null; instance_type: string | null; gpu_type: string | null; gpu_count: number | null; node_count: number | null };
  datasets: { name: string; version: string | null; uri: string | null; role: string }[];
  models: { name: string; version: string | null; uri: string | null }[];
  parent: RunRef | null;
  children: RunRef[];
  failure: { message: string; category: string | null; log_excerpt: string | null } | null;
  summary_metrics: Record<string, number>;
  backend_extras?: Record<string, string>;
  favorite?: boolean;
}

export interface SearchResponse { total: number; took_ms: number; notes: string[]; runs: Run[] }

export interface Artifact { run: RunRef; path: string; kind: string; size_bytes: number | null; sha256: string | null }

export interface ConfigChange {
  path: string; kind: "added" | "removed" | "changed"; baseline: unknown; candidate: unknown;
  category: string; relative_change: number | null; is_noise: boolean;
}
export interface MetricDelta {
  metric: string; baseline: number; candidate: number; absolute: number; relative: number | null;
  verdict: "improved" | "regressed" | "unchanged" | "unknown"; higher_is_better: boolean | null;
  z_score: number | null; noise_floor: number | null; significance_basis: "threshold" | "replicates";
}
export interface Comparison {
  baseline: RunRef; candidate: RunRef; deltas: MetricDelta[];
  config_diff: { baseline: RunRef; candidate: RunRef; changes: ConfigChange[] };
  baseline_replicates: RunRef[]; duration_ratio: number | null;
}

export interface Evidence {
  id: string; label: Label; statement: string;
  source: { run: RunRef | null; metric: string | null; artifact: string | null; config_path: string | null; step_range: [number, number] | null };
  value: number | null; baseline_value: number | null; data: Record<string, unknown>;
}
export interface Hypothesis { id: string; statement: string; supporting_evidence: string[]; confidence: Confidence; falsifiable_by: string | null }
export interface Proposal {
  id: string; title: string; controlled_variable: string; fixed_variables: string[]; expected_outcome: string;
  measurements: string[]; acceptance_criterion: string; estimated_cost_gpu_hours: number | null; information_gain: number; priority: number;
}
export interface Finding {
  id: string; title: string; statement: string; category: string; severity: Severity; confidence: Confidence; label: Label;
  evidence: Evidence[]; hypotheses: Hypothesis[]; recommendations: Proposal[]; limitations: string[];
  provenance: { analysis: string; analysis_version: string; params_hash: string; runs: RunRef[]; created_at: string };
}
export interface PlotSpec {
  id: string; title: string; kind: "line" | "bar" | "scatter"; x_label: string; y_label: string;
  series: { name: string; points: [number | string, number][] }[];
}
export interface AnalysisResult { analysis: string; skipped_reason: string | null; notes: string[]; findings: Finding[]; plots: PlotSpec[] }
export interface AnalysisInfo {
  name: string; version: string; title: string; description: string; category: string; requires: string[];
  limitations: string[]; params: { properties?: Record<string, { default?: unknown; type?: string; title?: string }> };
}

export interface RunOverview {
  run: Run; metric_keys: string[]; summary: Record<string, number>;
  configs: { source: string; data: Record<string, unknown> }[]; artifacts: Artifact[];
  baseline: string | null; what_changed: Comparison | null; findings: Finding[];
}
export interface MetricSeriesDto { key: string; steps: number[]; values: (number | null)[]; timestamps: number[] | null; source: string }
export interface Backend {
  name: string; kind: string; title: string; workspace: string | null; resource_group: string | null;
  subscription_id: string | null; root: string | null; available: boolean; error: string | null;
}

export interface Claim {
  type: "FACT" | "INFERENCE" | "HYPOTHESIS" | "RECOMMENDATION"; text: string; evidence: string[];
  confidence: string | null; verified: boolean; problems: string[];
}
export interface Investigation {
  id: string; question: string; prompt: string | null; persona: string; model: string; created_at: string; context_hash: string;
  summary: string; insufficient_evidence: boolean; parse_error: string | null; verified_ratio: number; claims: Claim[];
  evidence: { id: string; kind: string; text: string; source: Record<string, unknown> }[];
  tool_calls: { tool: string; args: Record<string, unknown>; evidence_id: string; ok: boolean }[];
  usage: { prompt_tokens: number; completion_tokens: number };
}
export interface InvestigationSummary { id: string; question: string; summary: string; model: string; created_at: string; verified_ratio: number; prompt: string | null; persona: string }
export interface PromptTemplate {
  id: string; version: number; title: string; tags: string[]; requires: string[]; template: string;
  variables: string[]; category: string; description: string; pack: string; needs_baseline: boolean;
  include: { logs: string; series: string[]; config_keys: string[]; slices: boolean };
}

export interface Job {
  id: string; kind: string; title: string; status: "running" | "done" | "failed" | "cancelled"; error: string | null; created_at: number;
  progress: { done_files?: number; total_files?: number; done_bytes?: number; total_bytes?: number; cached_files?: number; throughput_bps?: number };
  result?: { files?: { path: string; local_path: string; sha256: string; from_cache: boolean }[]; failed?: [string, string][]; indexed?: number };
}
export interface LLMSettings {
  enabled: boolean; base_url: string; model: string; api_key_env: string; temperature: number; max_tokens: number;
  timeout_s: number; context_limit_tokens: number; reasoning_effort: string | null; azure_api_version: string | null;
  max_tool_steps: number; has_session_key: boolean; has_env_key: boolean; active_model: string;
}
export interface Health { ok: boolean; version: string; backends: string[]; read_only: boolean; llm: string }
export interface Diagnostics {
  telemetry: { counters: Record<string, number>; latency: Record<string, { count?: number; p50_ms?: number; p95_ms?: number }> };
  index: Record<string, number>; artifact_cache_bytes: number;
  audit: { ts: string; actor: string; action: string; detail: Record<string, unknown> }[];
}
