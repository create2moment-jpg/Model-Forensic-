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
  /** On the run page only (the run list leaves them out): every job property (secrets hidden), inputs and outputs. */
  properties?: Record<string, string>;
  io?: JobIO[];
}
export interface JobIO { name: string; direction: "input" | "output"; type: string | null; asset: string | null; version: string | null; path: string | null; mode: string | null; value: string | null }

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
  set_aside?: { entry: string; reason: string };
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

export interface RunOverview { headline_groups?: HeadlineGroups | null;
  run: Run; metric_keys: string[]; summary: Record<string, number>;
  configs: { source: string; data: Record<string, unknown> }[]; artifacts: Artifact[];
  baseline: string | null; what_changed: Comparison | null; findings: Finding[]; knowledge?: AppliedKnowledge;
  /** Present on the progressive overview: which parts are loaded (nothing is shown as empty before it is). */
  ready?: { series: boolean; configs: boolean; artifacts: boolean; comparison: boolean };
}
export interface MetricColumns {
  columns: { key: string; runs: number; coverage: number }[]; default: string[]; headline: string[]; runs: number; runs_with_values: number;
  diagnosis: { check: string; backend: string; severity: string; runs_without_values: number; runs: number; cause: string; fix: string; fillable: boolean; missing: string[] }[];
}
export interface ParseMessage {
  severity: "info" | "recoverable_warning" | "unsupported_schema" | "parse_failure" | "missing_data" | "critical"; code: string; message: string;
  file: string | null; expected: Record<string, unknown> | null; observed: Record<string, unknown> | null; remediation: string; blocking: boolean;
}
export interface Pct { n: number; p50: number | null; p90: number | null; p99: number | null; mean: number | null; min: number | null; max: number | null }
export interface TsProduction { kind: string; title: string; cost: string; why: string; light_mode: boolean; concurrency: number; depends_on: string[]; cacheable: boolean; cold: Pct; warm: Pct; measured_class: string | null }
export interface TsHypothesis { type: "cost_class" | "warm_speedup" | "under_ms"; expect?: string; value?: number }
export interface TsExperiment {
  id: string; kind: string; runs: string[]; repetitions: number; hypothesis: TsHypothesis | null; state: string; created_at: string; ended_at?: string;
  samples: { run: string; mode: "cold" | "warm"; rep: number; ms: number; error: string }[]; error: string; environment: string[];
  stats?: { cold: Pct; warm: Pct }; measured_class?: string | null; light_mode_eligible?: boolean; errors?: number;
  verdict?: { result: "supported" | "refuted" | "inconclusive"; why: string }; task?: TaskView | null;
}
export interface TsExperimentSummary { id: string; kind: string; state: string; created_at: string; runs: number; samples: number; measured_class: string | null; verdict: string | null }
export interface TsProposal {
  id: string; version: number; kind: string; status: "proposed" | "accepted" | "rejected"; created_at: string; created_by: string;
  changes: Record<string, { current: unknown; proposed: unknown }>; rationale: string;
  evidence: { experiment: string; cold: Pct; warm: Pct; runs: string[]; repetitions: number; environment: string[]; thresholds_ms: Record<string, number>; light_mode_max_ms: number; errors: number };
  review: { by: string; at: string; note: string } | null;
}
export interface TestSpaceHome { settings: Record<string, unknown> & { sample_runs: string[]; repetitions: number; min_samples: number; thresholds_ms: Record<string, number> }; production: TsProduction[]; experiments: TsExperimentSummary[]; proposals: TsProposal[]; hypotheses: string[] }
export interface PlotSpecV1 {
  version?: 1; id: string; title: string; question?: string; kind?: "line" | "scatter"; x?: { label?: string; scale?: "linear" | "log" }; y?: { label?: string; scale?: "linear" | "log" };
  series: { run: string; key: string; label?: string; transform?: string; ema_alpha?: number; stage?: string }[]; annotations?: { step: number; label?: string }[]; source?: string;
}
export interface PlotRender { spec: PlotSpecV1; series: { label: string; run: string; key: string; transform: string; x: number[]; y: (number | null)[]; points_total: number }[]; warnings: string[] }
export interface PlotValidation { ok: boolean; errors: string[]; warnings: string[]; spec: PlotSpecV1 | null }
export interface UwHead { head: string; term: string; key: string; log_sigma: { first: number; last: number }; weight: { first: number; last: number }; share: number; drift_last_10pct: number; unweighted_loss_progress: number | null; status: "switched_off" | "at_bound" | "dominant" | "drifting" | "settled"; why: string;
  share_min?: number; share_max?: number; switched_off_at?: number | null; settled_at?: number | null; optimum_gap?: number | null; sigma_loss_corr?: number | null;
  baseline_share?: number | null; share_delta?: number | null; baseline_status?: string | null }
export interface UwTimeline { steps: number[]; share: Record<string, number[]>; contribution: Record<string, (number | null)[]> | null; effective_tasks: number[]; balance: number[];
  balance_summary: { effective_tasks_start: number; effective_tasks_end: number; balance_start: number; balance_end: number; max_min_ratio_end: number; heads: number } }
export interface UncertaintyReport { available: boolean; message?: ParseMessage; run?: string; heads?: UwHead[]; summary?: Record<string, number>; config?: Record<string, unknown>; bounds?: number[] | null; findings?: Finding[]; plots?: PlotSpecV1[]; formula?: string; timeline?: UwTimeline | Record<string, never>;
  baseline?: { run: string; error?: string; summary?: Record<string, number>; balance?: UwTimeline["balance_summary"] | null; only_here?: string[]; only_there?: string[] } | null }
export interface SetupClues { run: string; clues: { id: string; label: string; value: unknown; source: string; also: string[] }[]; not_found: string[]; observations: { id: string; severity: string; text: string; evidence: string[] }[]; config_keys: number; note: string }
export interface ConfigCheck { id: string; area: string; title: string; severity: "critical" | "warning" | "info"; status: "pass" | "fail" | "unknown" | "skip"; current: string; expected: string; remediation: string; prompt: string; prompt_path: string; validation: string }
export interface ConfigChecks { checks: ConfigCheck[]; failed: number; critical: number; ok: boolean }
export type TaskState = "queued" | "waiting" | "running" | "done" | "failed" | "cancelled";
export interface TaskView {
  id: string; kind: string; subject: string; title: string; cost: "lightweight" | "medium" | "heavy" | "very_heavy" | string; why: string;
  state: TaskState; created_at: number; started_at: number | null; ended_at: number | null; last_activity: number;
  progress: { step?: string } & Record<string, unknown>; cache: "cold" | "warm" | "unknown" | string; error: string; waiting_on: string[];
  result: Record<string, unknown>; elapsed_s: number; active: boolean;
}
export interface RunLight { run: Run; id: string; stages_known: { run: string }[]; cached: { series: boolean; configs: boolean; artifacts: boolean }; summary_metrics: Record<string, number>; headline?: string[]; metrics_total?: number;
  headline_groups?: HeadlineGroups | null }
/** The Overview's headline: combined metrics, then one metric per class (config/team.local.yaml → overview). */
export interface HeadlineGroups { combined: string[]; per_class: { class: string; key: string }[]; per_class_metric: string; keys: string[] }
export interface ActiveTask { source: "run" | "prefetch" | "job"; id: string; title: string; subject: string; state: string; elapsed_s: number; progress: Record<string, unknown>; link: string; kind?: string }
export interface MetricSeriesDto { key: string; steps: number[]; values: (number | null)[]; timestamps: number[] | null; source: string; points_total?: number; non_finite?: number }
export interface ChartReading { key: string; direction: "higher" | "lower" | "unknown"; points: number; non_finite: number; facts: string[]; warnings: string[]; markers: { step: number; kind: string; label: string }[]; checks: string[] }
export interface RunsReading { key: string; direction: "higher" | "lower" | "unknown"; runs: number; facts: string[]; warnings: string[]; checks: string[]; ranking: { run: string; final: number; best: number | null; plateau_step: number | null }[] }
export interface ChartAnswer { summary: string; claims: Claim[]; verified_ratio?: number; evidence: { id: string; text: string }[]; model?: string }
export interface Backend {
  name: string; kind: string; title: string; workspace: string | null; resource_group: string | null;
  subscription_id: string | null; root: string | null; available: boolean; verified?: boolean; error: string | null;
  diagnosis?: Diagnosis | null;
  runs?: number; last_sync?: string | null; syncing?: boolean;
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
  id: string; kind: string; title: string; status: "running" | "done" | "failed" | "cancelled" | "interrupted"; error: string | null; created_at: number; rerunnable?: boolean; run?: string | null;
  progress: { done_files?: number; total_files?: number; done_bytes?: number; total_bytes?: number; cached_files?: number; throughput_bps?: number };
  result?: { root?: string; files?: { path: string; run?: string; stage?: string | null; local_path: string; rel_path?: string; sha256: string; from_cache: boolean }[]; failed?: [string, string][]; indexed?: number };
}
export interface LLMSettings {
  enabled: boolean; base_url: string; model: string; api_key_env: string; temperature: number; max_tokens: number;
  timeout_s: number; context_limit_tokens: number; reasoning_effort: string | null; azure_api_version: string | null;
  max_tool_steps: number; has_session_key: boolean; has_env_key: boolean; active_model: string;
}
export interface LlmStatus { ok: boolean; state: "ok" | "failed" | "offline"; model: string; latency_ms?: number; at: string; error: string | null }
export interface Health { ok: boolean; version: string; backends: string[]; read_only: boolean; llm: string; llm_enabled?: boolean; llm_status?: LlmStatus | null; local_llm_url?: string; source_rev?: string | null; web_rev?: string | null; web_stale?: boolean }
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
export interface ProjectInfo { id: string; title: string; description: string; contract_version: number; workspaces: string[]; experiments: string[]; headline: string; aliases: number; terminology: Record<string, string>; unavailable: string[] }
export interface StorageRole { role: "artifacts" | "meta" | "index" | "work"; title: string; speed: "fast" | "bulk"; delete: string; path: string; exists: boolean; creatable: boolean; writable: boolean; free_bytes: number | null; device: string; ok: boolean; problems: string[]; notes: string[]; used_bytes: number; configured: boolean }
export interface StorageInfo {
  data_dir: string; roles: StorageRole[]; files: { path: string; kind: string; what: string }[];
  workers: { cpus: number; device: string; current: number; recommended: number; boost: number; boost_implications: string; why: string };
  saved_to?: string; restart_required?: boolean;
}
export interface TimelineSpan { name: string; start: string | null; end: string | null; duration_s: number | null; source: string; estimated?: boolean; note?: string }
export interface TimelineEvidence { files: number; configs: number; logs: number; code_diff: string | null; metrics: number | null }
export interface TimelineStage extends TimelineSpan { role: string; status: RunStatus; run?: string; command?: string | null; environment?: string | null; compute?: string | null; sub_stages: TimelineSpan[]; evidence: TimelineEvidence; failure?: string | null; error?: string }
export interface RunTimeline {
  run: { id: string; name: string; experiment: string | null; user: string | null; status: RunStatus; created: string | null; started: string | null; ended: string | null; duration_s: number | null; current_stage: string | null; command: string | null; environment: string | null; compute: string | null; project: string | null };
  stages: TimelineStage[]; sub_stages: TimelineSpan[]; evidence: TimelineEvidence; code_diffs: { run: string; stage: string | null; path: string; size: number | null }[];
}
export interface CodeDiff { available: boolean; searched?: string[]; run?: string; stage?: string | null; path?: string; size?: number | null; truncated?: boolean; stats?: { files: number; additions: number; deletions: number }; text?: string; label?: string }
export type PrefetchState = "planned" | "running" | "stopping" | "stopped" | "completed" | "failed" | "cancelled";
export interface PrefetchSummary {
  id: string; run: string; title: string; profile: string; state: PrefetchState; resumable: boolean; created_at: string; updated_at: string;
  files_total: number; files_selected: number; files_reused: number; files_done: number; files_partial: number; files_failed: number; files_remaining: number;
  bytes_selected: number; bytes_reused: number; bytes_remaining: number; unknown_sizes: number; throughput_bps: number;
  eta_s: number | null; eta_from: string | null; elapsed_s: number; workers: number; free_bytes: number | null; risks: string[]; note: string; error: string;
  phase?: PrefetchPhase; phase_elapsed_s?: number; phase_times?: Record<string, number>; heartbeat_age_s?: number | null; alive?: boolean | null;
  stalled?: boolean; retries?: number; item_states?: Record<string, number>; next?: string;
  /** A fraction only when every selected size is known — otherwise counts and an indeterminate bar. */
  progress?: { determinate: boolean; fraction: number | null; files_done: number; files_selected: number; bytes_done: number; bytes_total: number | null };
}
export type PrefetchPhase = "planned" | "manifesting" | "awaiting_selection" | "queued" | "downloading" | "validating" | "completed" | "stopping" | "stopped" | "cancelled" | "failed";
export interface PrefetchDiagnosis { cause: string; severity: "critical" | "warning" | "info"; evidence: string; fix: string }
export interface PrefetchEvent { at: string; kind?: string; text: string; path?: string; phase?: string; files?: string[] }
export interface PrefetchItem { key: string; run: string; stage: string | null; path: string; kind: string; size: number | null; selected: boolean; state: "pending" | "queued" | "downloading" | "validating" | "partial" | "reused" | "done" | "failed" | "cancelled"; partial_bytes: number; sha256: string; local_path: string; rel_path?: string; error: string; bytes_done?: number; attempts?: number; started_at?: number | null; ended_at?: number | null }
export interface PrefetchTask extends PrefetchSummary {
  local_root?: string; items: PrefetchItem[]; events: PrefetchEvent[]; diagnosis?: PrefetchDiagnosis[];
  in_flight?: { path: string; stage: string | null; bytes: number; size: number | null; state: string; attempts: number; for_s: number }[];
}
export interface DraftSummary { id: string; owner: string; kind: string; title: string; route: string; created_at: string; updated_at: string }
export interface WorkInProgress {
  drafts: DraftSummary[]; drafts_total: number;
  studies: (StudySummary & { next: string; gate_blocking: number; decision: string | null })[]; studies_open: number;
  jobs: { id: string; kind: string; title: string; status: string; error: string | null; created_at: number; rerunnable: boolean }[];
  knowledge_proposed: number;
}
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
export interface MetricNamesDto { only_reference: string[]; only_candidate: string[]; raw_of: Record<string, string>; matched: number; suggestions: { raw: string; key: string; to: string; score: number; why: string }[]; aliases: Record<string, string>; path: string }
export interface LocalPreview { ok: boolean; path: string; problem: string; runs: { path: string; name: string; run_id: string; metrics: string[]; configs: string[]; logs: string[] }[] }
export interface LocalRunsState { folders: string[]; path: string; backend: string; runs: { ref: string; name: string; path: string | null; status: string; kind?: string; found?: string; experiment?: string | null; updated_at?: string | null }[]; error: string | null }
export interface AzureAvailable { subscriptions: { id: string; name: string; tenant_id: string | null; default: boolean; state: string | null }[]; subscription_id: string; workspaces: (AzureWorkspaceCfg & { location?: string | null })[] }
export interface AzureDetected { subscription_id: string; subscription_name: string | null; tenant_id: string | null; user: { name?: string; mail?: string; upn?: string }; workspaces: AzureWorkspaceCfg[] }

export interface StageInfo { name: string; ref: string; status: string; kind: string; duration_s: number | null; failure: string | null }

export interface DownloadPlan { label: string; help: string; files: number; bytes: number; unknown_size: number; cached: number }
export interface DownloadPlans { profiles: Record<string, DownloadPlan>; stages: string[]; local_root: string }

export interface SeqClassRow { class: string; sequences: number; AP?: number | null; precision?: number | null; recall?: number | null; fp_per_gt?: number | null; num_gt?: number; num_tp?: number; num_fp?: number; num_fn?: number; [k: string]: unknown }
export interface SeqTable { path: string; stage: string | null; kind: string; recognized: boolean; note: string; rows?: number; sequences?: number; classes?: string[]; headline?: Record<string, number>; metric?: string; metrics?: string[]; per_class?: SeqClassRow[] }
export interface SequencesResponse { pending?: import("../components/PendingDownload").Pending; messages?: ParseMessage[]; tables: SeqTable[]; selected?: number; worst?: Record<string, string | number>[]; deprioritized?: string[] }

export interface TeamPolicy { deprioritized_classes: string[]; expected_metrics: Record<string, Record<string, string>>; code_refs: Record<string, string>; analysis_params: Record<string, Record<string, unknown>>; source: string | null; path: string }

export interface CheckCard { light: "red" | "amber" | "green"; title: string; meaning: string; why: string; todo: string; learn_more: string[]; terms: string[]; analysis: string; severity: string }
export interface CheckResult { pending?: import("../components/PendingDownload").Pending[]; run: { ref: string; name: string; status: string; user: string | null }; baseline: string | null; overall: "red" | "amber" | "green"; summary: string; cards: CheckCard[]; more: number; checked: string[]; glossary: Record<string, string>; set_aside?: { title: string; entry: string; reason: string }[]; knowledge?: { text: string; entry: string }[] }

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
export interface PrecisionRecall { pending?: import("../components/PendingDownload").Pending[]; classes: string[]; deprioritized: string[]; stages: string[]; working_points: WorkingPoint[]; auc: AucCell[]; counts: ClassCounts[]; notes: string[]; caveats: string[] }
export interface PrLogLine { stage: string; path: string; line_no: number; text: string }
export interface EvalFile { path: string; ref: string; stage: string; size_bytes: number | null; cached: boolean; family: string; purpose: string; caveat: string; class: string | null }
export type Preview = { kind: "text"; text: string; total_lines: number; truncated: boolean } | { kind: "image"; url: string } | { kind: "table"; columns: string[]; rows: Record<string, unknown>[]; total_rows: number } | { kind: "too_large" | "binary" | "unsupported"; size_bytes?: number; note?: string }

export interface KnowledgeEntry {
  id: string; kind: "constraint" | "fact" | "setup" | "caveat" | "code_ref" | "belief"; statement: string; why: string;
  when: { setup?: string[]; dataset_contains?: string[]; stage_contains?: string[]; config?: Record<string, string>; tags?: Record<string, string> };
  effects: { ignore_metrics?: string[]; ignore_classes?: string[]; caveat?: string; setup?: string };
  source: { by?: string; at?: string; evidence?: string; code_refs?: string[]; via?: string };
  confidence: "low" | "medium" | "high"; status: "proposed" | "active" | "retired";
  origin?: string;
  test?: BeliefTest | null;
}
export interface BeliefTest {
  type: "config_effect" | "loss_behaviour"; key?: string; change?: "increase" | "decrease" | "set"; value?: string; metric?: string;
  expect?: "up" | "down" | "no_change"; min_effect?: number; family?: string;
  behaviour?: "muted" | "trusted" | "plateaus_early" | "still_learning" | "rising"; scope?: string; max_runs?: number;
}
export type BeliefColour = "dark_green" | "green" | "yellow" | "red" | "grey";
export interface BeliefVerdictSummary { id: string; at: string; test_hash: string; p: number; lo: number; hi: number; colour: BeliefColour; n: number; support: number; contra: number; no_effect: number }
export interface BeliefEvidence { kind: "pair" | "run"; runs: string[]; outcome: "supports" | "contradicts" | "no_effect" | "skipped"; weight: number; detail: string; other_diffs: number; change: string; delta: number | null }
export interface BeliefVerdict { p: number; lo: number; hi: number; p_data: number; colour: BeliefColour; support: number; contra: number; no_effect: number; skipped: number; n: number; threshold: number; summary: string; next_experiment: string; evidence: BeliefEvidence[]; runs_considered: number }
export interface BeliefItem { entry: KnowledgeEntry; verdict: BeliefVerdictSummary | null; stale: boolean; testable: boolean }
export interface BeliefDetail { entry: KnowledgeEntry; stale: boolean; latest: { at: string; verdict: BeliefVerdict; history: { at: string; p: number; colour: BeliefColour; n: number }[] } | null }
export interface BeliefVocabulary { config_keys: { key: string; numeric: boolean }[]; metrics: string[]; families: string[] }
export interface KnowledgeSourceStatus {
  name: string; path: string; resolved: string | null; exists: boolean; kind: "auto" | "knowledge" | "docs";
  trust: "proposed" | "as_written"; codebase: string; inbox: boolean; env: string; entries: number; docs: number; problems: string[];
}
export interface SourceReport { source: string; path: string; exists: boolean; files: number; changed: number; removed: number; seconds: number; error: string; notes: string[] }
export interface DocHit { source: string; path: string; title: string; heading: string; anchor: string; snippet: string; codebase: string; score: number }
export interface DocFile { source: string; path: string; title: string; codebase: string; mtime: number; size: number }
export interface AppliedKnowledge { entries: KnowledgeEntry[]; setups: string[]; ignore_metrics: { glob: string; entry: string }[]; ignore_classes: { class: string; entry: string }[]; caveats: { text: string; entry: string }[] }

export interface LogFileInfo { id: string; path: string; stage: string | null; run: string; size_bytes: number | null; group: string; label: string; failed_stage: boolean }
export interface LogCatalog { files: LogFileInfo[]; recommended: string | null; stages: string[]; stage: string | null }
export interface LogText { id: string; path: string; stage: string | null; run: string; size_bytes: number | null; read_bytes: number; partial: boolean; text: string }
export interface FolderListing { ok: boolean; path: string; problem?: string; parent: string | null; dirs: { name: string; path: string; run: string }[]; truncated?: boolean; run?: string; home: string; roots: string[] }

export interface VerdictNumber { key: string; label: string; value: number; text: string; baseline: number | null; delta_pct: number | null; higher_is_better: boolean | null; good: boolean | null; noise?: boolean; spark: number[]; points: number }
export interface Verdict {
  run: string; status: string; stage: string | null;
  state: "healthy" | "attention" | "problem" | "running" | "failed" | "not_loaded" | "no_data";
  sentence: string; lead: string; detail: string;
  headline: VerdictNumber[];
  baseline: { ref: string; why: string; name: string } | null;
  next: { text: string; tab?: string; view?: string; keys?: string[] } | null;
  problems: { id: string; status: string; title: string; statement: string; keys: string[] }[];
  counts: Record<string, number>;
  remote: boolean;
}
export interface CompareVerdict { state: "better" | "worse" | "mixed" | "same" | "no_data"; sentence: string; detail: string; better: number; worse: number; judged: number; stage: string | null }

/** Continuous training: an experiment of a workspace followed run after run (``/tracks``). */
export interface Track { id: string; title: string; workspace: string; experiment: string; metrics: string[]; watch: boolean; owner?: string; created_at: string; updated_at: string;
  state?: TrackState; sentence?: string; latest?: string | null; health?: TrackHealth }
export type TrackState = "best" | "better" | "same" | "mixed" | "worse" | "failed" | "no_data";
export interface TrackHealth { runs: number; completed: number; failed: number; running: number; median_run_s: number | null; latest_run_s: number | null }
export interface TrackCompare { ref: string; state: "better" | "worse" | "mixed" | "same" | "no_data"; better: number; worse: number; within_noise: number; metrics: Record<string, { delta_pct: number | null; good: boolean | null; noise: boolean }> }
export interface TrackRun { ref: string; name: string; status: RunStatus; created_at: string | null; user: string | null; values: Record<string, number>; run_s: number | null; queue_s: number | null;
  git: string | null; branch: string | null; data: string[]; models: string[]; vs_previous: TrackCompare | null; vs_best: TrackCompare | null; best: boolean }
export interface TrackProgress { track: Track; metrics: { key: string; label: string; higher_is_better: boolean | null }[]; primary: string | null; runs: TrackRun[]; best: string | null; latest: string | null;
  sentence: string; state: TrackState; health: TrackHealth; missing_values: string[] }
