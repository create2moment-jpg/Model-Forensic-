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

export interface Artifact { run: RunRef; path: string; kind: string; size_bytes: number | null; sha256: string | null; stage?: string | null }

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
export interface MetricSeriesDto { key: string; steps: number[]; values: (number | null)[]; timestamps: number[] | null; source: string; points_total?: number; non_finite?: number }
export interface ChartReading { key: string; direction: "higher" | "lower" | "unknown"; points: number; non_finite: number; facts: string[]; warnings: string[]; markers: { step: number; kind: string; label: string }[]; checks: string[] }
export interface RunsReading { key: string; direction: "higher" | "lower" | "unknown"; runs: number; facts: string[]; warnings: string[]; checks: string[]; ranking: { run: string; final: number; best: number | null; plateau_step: number | null }[] }
export interface ChartAnswer { summary: string; claims: Claim[]; verified_ratio?: number; evidence: { id: string; text: string }[]; model?: string }
export interface Backend {
  name: string; kind: string; title: string; workspace: string | null; resource_group: string | null;
  subscription_id: string | null; root: string | null; available: boolean; verified?: boolean; error: string | null;
  diagnosis?: Diagnosis | null;
}
export interface Diagnosis { cause: string; summary: string; fixes: string[]; matched: string }
export interface ConnCheck { name: string; ok: boolean; seconds?: number | null; slow?: boolean; detail: string; diagnosis?: Diagnosis }
export interface DeepHealth {
  status: "ok" | "degraded" | "down"; version: string; uptime_s: number;
  backends: { name: string; ok: boolean; verified: boolean; error: string | null; diagnosis: Diagnosis | null }[];
  index: Record<string, number>; artifact_cache_bytes: number; disk: { free_gb?: number; total_gb?: number }; low_disk: boolean;
  scheduler: { last_sync: string | null }; llm: { model: string; enabled: boolean }; notices: string[]; jobs_running: number;
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
  id: string; kind: string; title: string; status: "running" | "done" | "failed" | "cancelled" | "interrupted"; error: string | null; created_at: number; rerunnable?: boolean;
  progress: { done_files?: number; total_files?: number; done_bytes?: number; total_bytes?: number; cached_files?: number; throughput_bps?: number };
  result?: { files?: { path: string; run?: string; stage?: string | null; local_path: string; sha256: string; from_cache: boolean }[]; failed?: [string, string][]; indexed?: number };
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

export type HypothesisStatus = "open" | "supported" | "rejected" | "inconclusive";
export interface EvidenceLink { kind: "finding" | "claim" | "metric" | "run" | "note"; ref: string; label: string; run: RunRef | null; supports: boolean }
export interface StudyHypothesis {
  id: string; statement: string; status: HypothesisStatus; rationale: string; evidence: EvidenceLink[]; proposed_change: string;
  reference_run: RunRef | null; test_run: RunRef | null; verdict: string; source: string; created_at: string; updated_at: string;
}
export interface Study {
  id: string; title: string; goal: string; created_at: string; updated_at: string; runs: RunRef[]; baseline: RunRef | null;
  hypotheses: StudyHypothesis[]; notes: { id: string; text: string; created_at: string; author?: string }[]; owner?: string; pinned_findings: string[];
  investigations: string[]; timeline: { at: string; kind: string; text: string }[];
  preregistration?: Preregistration | null; gate?: StudyGate | null; decision?: StudyDecision | null;
  status?: "open" | "closed";
  comparisons?: SavedComparison[];
}
export interface CatalogFamily { family: string; answers: string; worth: string; caveat: string; files: number; bytes: number; unsized: number; cached: number; stages: string[]; classes: string[]; paths: string[] }
export interface RunCatalog { families: CatalogFamily[]; files: number; bytes: number }
export interface SavedComparison { id: string; runs: string[]; reference: string; keys: string[]; summary: string[]; rows: string[]; note: string; created_at: string; author: string }
export interface Preregistration { template: string; question: string; claim: string; deciding_metric: string; ruler: string | null; falsified_if: string; confounders: string[]; confounders_checked: string[]; analyses: string[]; registered_at: string }
export interface StudyGate { checked_at: string; checks: { id: string; title: string; status: string; run: string; detail: string }[]; blocking: string[] }
export type DecisionOutcome = "adopt" | "reject" | "more_evidence" | "inconclusive";
export interface StudyDecision { outcome: DecisionOutcome; decision: string; rationale: string; based_on: string[]; gate_override: string; decided_at: string }
export interface QuestionTemplate { id: string; title: string; question: string; claim: string; falsified_if: string; confounders: string[]; analyses: string[]; needs_baseline: boolean; default_metric: string; hint: string }
export interface StudySummary { id: string; title: string; goal: string; updated_at: string; runs: number; hypotheses: Record<HypothesisStatus, number>; status?: "open" | "closed" }
export interface StudyEvidenceRow { run: Run; summary: Record<string, number>; deltas: MetricDelta[]; config_changes: ConfigChange[]; is_baseline: boolean; findings: Finding[] }
export interface StudyEvidence { study: Study; runs: StudyEvidenceRow[] }
export interface VerifyResult { hypothesis: StudyHypothesis; comparison: Comparison; suggestion: string }

export interface AzureWorkspaceCfg {
  name: string; title?: string | null; subscription_id: string; resource_group: string; workspace: string;
  default?: boolean; credential?: "cli" | "default" | "managed_identity" | null; tenant_id?: string | null;
  placeholder?: boolean; studio_url?: string; location?: string | null;
}
export interface AzureDefaultsCfg { credential: "cli" | "default" | "managed_identity"; tenant_id: string | null; metrics_api: "runhistory" | "mlflow"; fast_listing: boolean; me?: string[] }
export interface AzureSettings { source: string | null; path: string; defaults: Partial<AzureDefaultsCfg>; workspaces: AzureWorkspaceCfg[]; notices?: string[]; error?: string }
export interface AzureDetected { subscription_id: string; subscription_name: string | null; tenant_id: string | null; user: { name?: string; mail?: string; upn?: string }; workspaces: AzureWorkspaceCfg[] }

export interface StageInfo { name: string; ref: string; status: string; kind: string; duration_s: number | null; failure: string | null }

export interface DownloadPlan { label: string; help: string; files: number; bytes: number; unknown_size: number; cached: number }
export interface DownloadPlans { profiles: Record<string, DownloadPlan>; stages: string[]; local_root: string }

export interface SeqClassRow { class: string; sequences: number; AP?: number | null; precision?: number | null; recall?: number | null; fp_per_gt?: number | null; num_gt?: number; num_tp?: number; num_fp?: number; num_fn?: number; [k: string]: unknown }
export interface SeqTable { path: string; stage: string | null; kind: string; recognized: boolean; note: string; rows?: number; sequences?: number; classes?: string[]; headline?: Record<string, number>; metric?: string; metrics?: string[]; per_class?: SeqClassRow[] }
export interface SequencesResponse { tables: SeqTable[]; selected?: number; worst?: Record<string, string | number>[]; deprioritized?: string[] }

export interface TeamPolicy { deprioritized_classes: string[]; expected_metrics: Record<string, Record<string, string>>; code_refs: Record<string, string>; analysis_params: Record<string, Record<string, unknown>>; source: string | null; path: string }

export interface CheckCard { light: "red" | "amber" | "green"; title: string; meaning: string; why: string; todo: string; learn_more: string[]; terms: string[]; analysis: string; severity: string }
export interface CheckResult { run: { ref: string; name: string; status: string; user: string | null }; baseline: string | null; overall: "red" | "amber" | "green"; summary: string; cards: CheckCard[]; more: number; checked: string[]; glossary: Record<string, string> }

export interface NextExperiment { source: "rule" | "ai" | "none"; title: string; changes: { path: string; current: unknown; proposed: unknown; why: string }[]; rejected: string[]; expected_outcome: string; measurements: string[]; yaml: string }

export interface DivergenceRow {
  run: string; outcome: "same" | "ahead" | "behind" | "closed" | "crossed" | "insufficient";
  diverge_step: number | null; diverge_fraction: number | null; final_gap: number | null; max_gap: number | null;
  threshold: number | null; sentence: string; lr_note: string; fits: string[];
  seed_n?: number; seed_z?: number | null; seed_verdict?: string;
}
export interface CurvesKey {
  key: string; reference: string; direction: "higher" | "lower" | "unknown"; summary: string; rows: DivergenceRow[];
  series: { run: string; ref: string; steps: number[]; values: (number | null)[] }[];
}
export interface CurvesComparison { reference: string; runs: { ref: string; label: string; status: string; alias?: string; stages?: string[]; stage?: string }[]; keys: CurvesKey[]; reference_seeds?: string[] }

export interface SplitFact { split: string; version: string; dataset_name: string; index_url: string; source: string }
export interface LogFileScan { stage: string; path: string; size_bytes: number | null; scanned: boolean; error?: string }
export interface DatasetStage {
  stage: string; catalog_basepath: string; dataset_name: string; use_local_dataset: boolean; local_basepath: string; local_name: string;
  local_configured: SplitFact[]; predict_usage: string; dataset_split: string; infer_latest_version: string; configured: SplitFact[];
  loaded: SplitFact[]; config_source: string; mismatches: string[]; notes: string[];
}
export interface DatasetInfo { stages: DatasetStage[]; notes: string[]; log_files: LogFileScan[] }
export interface GpuFact { name: string; memory_gib: string; memory_raw: string; driver_version: string; vbios_version: string; bus_id: string; pci_link_gen: string; source: string }
export interface ComputeStage {
  stage: string; gpus: GpuFact[]; gpu_log_files: number; cpu_only: boolean; parallelism: { key: string; value: string; source: string }[];
  config_source: string; platform: Record<string, string | number>; notes: string[];
}
export interface ComputeInfo { stages: ComputeStage[]; notes: string[]; log_files: LogFileScan[] }

export interface WorkingPoint { stage: string; class: string; target_recall: number; precision?: number; recall?: number; f1?: number; shortfall?: number }
export interface AucCell { stage: string; class: string; target_recall: number; distance_m: number; auc: number }
export interface ClassCounts { stage: string; class: string; sequences: number; num_tp: number; num_fp: number; num_fn: number; num_gt: number; precision: number | null; recall: number | null; fp_per_gt: number | null }
export interface PrecisionRecall { classes: string[]; deprioritized: string[]; stages: string[]; working_points: WorkingPoint[]; auc: AucCell[]; counts: ClassCounts[]; notes: string[]; caveats: string[] }
export interface PrLogLine { stage: string; path: string; line_no: number; text: string }
export interface EvalFile { path: string; ref: string; stage: string; size_bytes: number | null; cached: boolean; family: string; purpose: string; caveat: string; class: string | null }
export type Preview = { kind: "text"; text: string; total_lines: number; truncated: boolean } | { kind: "image"; url: string } | { kind: "table"; columns: string[]; rows: Record<string, unknown>[]; total_rows: number } | { kind: "too_large" | "binary" | "unsupported"; size_bytes?: number; note?: string }
