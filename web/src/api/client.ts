import type { AzureAvailable, MetricNamesDto, LocalPreview, LocalRunsState, FolderListing,
  AnalysisInfo, AnalysisResult, Backend, EvidenceLink, HypothesisStatus, Run, Study, StudyEvidence, StudySummary, VerifyResult, Comparison, Diagnostics, Health, Investigation, InvestigationSummary, Job, LLMSettings,
  MetricSeriesDto, PromptTemplate, RunOverview, SearchResponse, AzureSettings, AzureDetected, AzureDefaultsCfg, AzureWorkspaceCfg, StageInfo, DownloadPlans, SequencesResponse, TeamPolicy, CheckResult, QuestionTemplate, DecisionOutcome, NextExperiment, DeepHealth, ChartReading, ChartAnswer, RunsReading, CurvesComparison, DatasetInfo, ComputeInfo, PrecisionRecall, RunCatalog, KnowledgeEntry, AppliedKnowledge, DraftSummary, WorkInProgress, LlmStatus, PrefetchSummary, PrefetchTask, ProjectInfo, StorageInfo, StorageRole, RunTimeline, CodeDiff, KnowledgeSourceStatus, SourceReport, BeliefItem, BeliefDetail, BeliefVocabulary, DocHit, DocFile, PrLogLine, LogFileScan, LogCatalog, LogText, Verdict, CompareVerdict, EvalFile, Preview, RunLight, TaskView, ActiveTask, MetricColumns, TestSpaceHome, TsExperiment, TsProposal, TsHypothesis, PlotSpecV1, PlotRender, PlotValidation, UncertaintyReport, SetupClues, ConfigChecks } from "./types";

/** The selected project (header) scopes every run search and experiment list; null = all projects. */
let activeProject: string | null = null;
export const setActiveProject = (p: string | null): void => { activeProject = p; };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit & { raw?: boolean }): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  if (!res.ok) {
    let code = "http_error";
    let message = `${res.status} ${res.statusText}`;
    let details: unknown;
    try {
      const body = (await res.json()) as { error?: { code: string; message: string; details?: unknown }; detail?: string };
      if (body.error) ({ code, message, details } = body.error);
      else if (body.detail) message = String(body.detail);
    } catch { /* non-JSON error */ }
    throw new ApiError(res.status, code, message, details);
  }
  if (init?.raw) return (await res.text()) as T;
  return (await res.json()) as T;
}

const q = (params: Record<string, string | number | boolean | undefined | null | string[]>): string => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
};
const enc = encodeURIComponent;
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export interface FacetSpecDto { aliases: string[]; values: string[]; value_aliases: Record<string, string>; from_name: boolean; only_listed: boolean }
export interface FacetConfigDto { order: string[]; hide_dims: string[]; facets: Record<string, FacetSpecDto>; sources: string[]; notice: string | null }

export interface DynFit { l0: number; a: number; alpha: number; c: number; r2: number; usable: boolean; anneal_part: number; end_fitted: number; notes: string[] }
export interface DynamicsDto {
  lr_logged: boolean; readings: string[];
  fits: { key: string; fit: DynFit | null; sentence: string; anneal_share?: number; longer_1_5x?: number | null; floor_identified?: boolean }[];
  effects: { key: string; verdict: string; sentence: string; observed_gap?: number; equal_schedule_gap?: number; schedule_share?: number; extrapolated?: boolean }[];
  rates: { key: string; group: string; ratio_end: number; rate_end: number; lag_from: number | null; status: string; curve: number[]; base_rate_end: number | null }[];
}
export interface LossQuestionDto { id: string; area: string; question: string; status: "bad" | "warn" | "ok" | "na"; answer: string; keys: string[]; look: string; ideas: string[]; why: string }

export interface LedgerMoved { metric: string; baseline: number; candidate: number; relative: number | null; verdict: string; z: number | null }
export interface LedgerOutcome { candidate: string; computed_at: string; error?: string; config_changes: { path: string; category: string; text: string }[]; config_categories: Record<string, number>; improved: number; regressed: number; significance: string; moved: LedgerMoved[]; problems: { id: string; status: string; question: string; answer: string }[] }
export interface LedgerEntry { id: string; owner: string; created_at: string; updated_at: string; title: string; hypothesis: string; change: string; change_auto?: string; baseline: string | null; candidates: string[]; status: string; decision: string; decision_note: string; notes: string; tags: string[]; study?: string | null; outcome: Record<string, LedgerOutcome>; history: { at: string; by: string; what: string }[] }
export interface LedgerSummary { id: string; title: string; status: string; decision: string; baseline: string | null; candidates: string[]; tags: string[]; owner: string; created_at: string; updated_at: string; headline: string }

export interface AttnTable { layers: string[]; modalities: string[]; mass: Record<string, Record<string, { end: number | null; start: number | null }>>; entropy: Record<string, number | null>; entropy_mod: Record<string, Record<string, number | null>>; top1: Record<string, number | null>; keys: Record<string, Record<string, string>> }
export interface QueryRow { key: string; end: number | null; start: number | null }
export type StabTable = Record<string, Record<string, { start: number | null; end: number | null; key: string; rising: boolean }>>;
export interface InternalsDto { logged: boolean; attention: AttnTable; queries: QueryRow[]; stability?: StabTable; notes: { status: string; id: string; text: string; check: string }[]; baseline?: { attention: AttnTable; queries: QueryRow[]; stability?: StabTable } }

export interface DriftRow { key: string; family: string; outcome: string; diverge_fraction: number | null; final_gap: number | null; max_gap: number | null; threshold: number | null; sentence: string; shape_notes: string[]; lr_note: string; fits: string[] }
export interface DriftDto { rows: DriftRow[]; aligned_by: string; readings: string[]; summary: string }

export interface ExpCheck { id: string; term: string; kind: string; status: "bad" | "warn" | "ok" | "na"; text: string; hypothesis: string; expected: unknown; actual: unknown; keys: string[]; code_refs: string[] }
export interface ExpTerm { name: string; key: string | null; points: { steps: number[]; values: number[] }; formula: string; weight: number | null; initial: { value: number; tolerance: number; why: string } | null; range: { min: number | null; max: number | null } | null; direction: string; code_refs: string[]; why: string }
export interface ExpectationsDto { found: boolean; where: string; brief_command: string; errors: string[]; file?: string; codebase?: string; generated_by?: string; generated_at?: string; config_source?: string; notes?: string[]; checks?: ExpCheck[]; terms?: ExpTerm[]; counts?: { bad: number; warn: number; ok: number } }

export interface ObservationDto { id: string; source: "health" | "drift" | "code" | "internals" | "finding" | "comparison"; status: "bad" | "warn" | "ok" | "na" | "info"; title: string; statement: string; evidence: string[]; cause: string; next_step: string; why: string; look: string; code_refs: string[]; measure: number | null; param: string; stage: string | null }

export const api = {
  health: () => request<Health>("/health"),
  /** ``top``: pipelines and plain runs — a pipeline's stages are listed under it (``parent:<id>``), not beside it. */
  search: (query: string, limit = 200, offset = 0, backend?: string | null, top = false) => request<SearchResponse>(`/runs${q({ q: query, limit, offset, backend, project: activeProject ?? undefined, top: top || undefined })}`),
  projects: () => request<{ contract_version: number; projects: ProjectInfo[]; notes: string[] }>("/projects"),
  backends: () => request<Backend[]>("/backends"),
  runInfo: (runs: string[]) => request<Run[]>(`/run-info${q({ runs })}`),
  metricKeys: (runs: string[]) => request<Record<string, number>>(`/metric-keys${q({ runs })}`),
  suggest: (prefix: string, field = "name") => request<string[]>(`/suggest${q({ prefix, field })}`),
  overview: (run: string, baseline?: string | null) => request<RunOverview>(`/runs/${enc(run)}${q({ baseline })}`),
  metricCatalog: (run: string) => request<{ stages: string[]; entries: { key: string; metric: string; stage: string | null; label: string; points?: number }[] }>(`/runs/${enc(run)}/metric-catalog`),
  runLight: (run: string) => request<RunLight>(`/runs/${enc(run)}/light`),
  runLoad: (run: string, mode: "light" | "full", kinds: string[] = [], baseline?: string | null) => request<{ tasks: TaskView[] }>(`/runs/${enc(run)}/load${q({ mode, kinds: kinds.length ? kinds : undefined, baseline: baseline || undefined })}`, { method: "POST" }),
  runTasks: (run: string) => request<{ tasks: TaskView[] }>(`/runs/${enc(run)}/tasks`),
  overviewCached: (run: string, baseline?: string | null) => request<RunOverview>(`/runs/${enc(run)}/overview-cached${q({ baseline })}`),
  metricColumns: (runs: string[]) => request<MetricColumns>("/runs/metric-columns", { method: "POST", body: JSON.stringify({ runs }) }),
  loadMetrics: (runs: string[]) => request<{ tasks: TaskView[] }>("/runs/load-metrics", { method: "POST", body: JSON.stringify({ runs }) }),
  fillSummaries: (runs: string[]) => request<{ tasks: TaskView[] }>("/runs/summaries", { method: "POST", body: JSON.stringify({ runs }) }),
  testSpace: () => request<TestSpaceHome>("/test-space"),
  tsStart: (body: { kind: string; runs: string[]; repetitions?: number; hypothesis?: TsHypothesis | null }) => request<TsExperiment>("/test-space/experiments", { method: "POST", body: JSON.stringify(body) }),
  tsExperiment: (id: string) => request<TsExperiment>(`/test-space/experiments/${enc(id)}`),
  tsPropose: (id: string) => request<TsProposal>(`/test-space/experiments/${enc(id)}/propose`, { method: "POST" }),
  tsReview: (id: string, decision: "accept" | "reject", note = "") => request<TsProposal>(`/test-space/proposals/${enc(id)}/${decision}`, { method: "POST", body: JSON.stringify({ note }) }),
  plotSuggest: (run: string, stage?: string | null) => request<{ specs: PlotSpecV1[]; note?: string }>(`/runs/${enc(run)}/plots/suggest${q({ stage })}`),
  plotAi: (run: string, question: string) => request<{ model: string | null; specs: PlotSpecV1[]; rejected: { spec: unknown; errors: string[] }[]; fallback?: PlotSpecV1[]; note: string }>(`/runs/${enc(run)}/plots/ai`, { method: "POST", body: JSON.stringify({ question }) }),
  plotValidate: (spec: unknown) => request<PlotValidation>("/plots/validate", { method: "POST", body: JSON.stringify(spec) }),
  plotRender: (spec: PlotSpecV1) => request<PlotRender>("/plots/render", { method: "POST", body: JSON.stringify(spec) }),
  uncertainty: (run: string, baseline?: string | null, stage?: string | null) => request<UncertaintyReport>(`/runs/${enc(run)}/uncertainty${q({ baseline, stage })}`),
  setupClues: (run: string) => request<SetupClues>(`/runs/${enc(run)}/setup-clues`),
  configChecks: () => request<ConfigChecks>("/checks"),
  tasksActive: () => request<{ active: ActiveTask[]; count: number }>("/tasks/active"),
  stages: (run: string) => request<StageInfo[]>(`/runs/${enc(run)}/stages`),
  favorite: (run: string, value: boolean) => request<unknown>(`/runs/${enc(run)}/favorite${q({ value })}`, { method: "PUT" }),
  explainChart: (run: string, key: string, baseline?: string | null) => request<ChartReading>(`/runs/${enc(run)}/metrics/explain${q({ key, baseline })}`),
  askChart: (run: string, key: string, question: string, baseline?: string | null) =>
    request<ChartAnswer>(`/runs/${enc(run)}/metrics/ask`, { method: "POST", body: JSON.stringify({ key, question, baseline }) }),
  explainRuns: (key: string, runs: string[]) => request<RunsReading>("/metrics/explain-runs", { method: "POST", body: JSON.stringify({ key, runs }) }),
  askRuns: (key: string, runs: string[], question: string) =>
    request<ChartAnswer>("/metrics/ask-runs", { method: "POST", body: JSON.stringify({ key, runs, question }) }),
  metrics: (run: string, keys: string[] = [], maxPoints = 2000) =>
    request<MetricSeriesDto[]>(`/runs/${enc(run)}/metrics${q({ keys, max_points: maxPoints })}`),
  facets: () => request<FacetConfigDto>("/facets"),
  ledger: (status?: string, text?: string) => request<{ entries: LedgerSummary[]; total: number }>(`/ledger${q({ status, text })}`),
  ledgerEntry: (id: string) => request<LedgerEntry>(`/ledger/${enc(id)}`),
  ledgerCreate: (body: Partial<LedgerEntry> & { analyse?: boolean }) => request<LedgerEntry>("/ledger", { method: "POST", body: JSON.stringify(body) }),
  ledgerUpdate: (id: string, body: Partial<LedgerEntry>) => request<LedgerEntry>(`/ledger/${enc(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  ledgerRefresh: (id: string) => request<LedgerEntry>(`/ledger/${enc(id)}/refresh`, { method: "POST" }),
  ledgerDelete: (id: string) => request<{ deleted: string }>(`/ledger/${enc(id)}`, { method: "DELETE" }),
  ledgerPromote: (id: string) => request<{ added: string[]; errors: string[] }>(`/ledger/${enc(id)}/promote`, { method: "POST" }),
  internals: (run: string, baseline?: string | null, stage?: string | null) => request<InternalsDto>(`/runs/${enc(run)}/internals${q({ baseline, stage })}`),
  lossDrift: (baseline: string, candidate: string, stage?: string | null) => request<DriftDto>(`/compare/loss-drift${q({ baseline, candidate, stage })}`),
  expectations: (run: string, stage?: string | null) => request<ExpectationsDto>(`/runs/${enc(run)}/expectations${q({ stage })}`),
  observations: (run: string, baseline?: string | null, stage?: string | null) => request<{ observations: ObservationDto[]; counts: Record<string, number> }>(`/runs/${enc(run)}/observations${q({ baseline, stage })}`),
  lossDynamics: (run: string, baseline?: string | null, stage?: string | null) => request<DynamicsDto>(`/runs/${enc(run)}/loss-dynamics${q({ baseline, stage })}`),
  lossQuestions: (run: string, baseline?: string | null, stage?: string | null) => request<{ questions: LossQuestionDto[] }>(`/runs/${enc(run)}/loss-questions${q({ baseline, stage })}`),
  flatConfig: (run: string) => request<Record<string, unknown>>(`/runs/${enc(run)}/config?flat=true`),
  resolve: (text: string) => request<{ kind: "run" | "runs" | "folder" | "none"; ref?: string; name?: string; how?: string; path?: string; why?: string; runs?: { ref: string; name: string }[] }>(`/resolve${q({ text })}`),
  verdict: (run: string, baseline?: string | null, stage?: string | null, cachedOnly = false) =>
    request<Verdict>(`/runs/${enc(run)}/verdict${q({ baseline: baseline || undefined, stage: stage || undefined, cached_only: cachedOnly || undefined })}`),
  compareSummary: (baseline: string, candidate: string, stage?: string | null) =>
    request<string>(`/compare/summary.md${q({ baseline, candidate, stage: stage || undefined })}`, { raw: true }),
  compareVerdict: (baseline: string, candidate: string, stage?: string | null) =>
    request<CompareVerdict>(`/compare/verdict${q({ baseline, candidate, stage: stage || undefined })}`),
  logFiles: (run: string, stage?: string | null) => request<LogCatalog>(`/runs/${enc(run)}/logs/files${q({ stage: stage || undefined })}`),
  logFile: (run: string, id: string, tailBytes: number) => request<LogText>(`/runs/${enc(run)}/logs/file${q({ id, tail_bytes: tailBytes })}`),
  logs: (run: string, tailBytes = 400_000) => request<string>(`/runs/${enc(run)}/logs${q({ tail_bytes: tailBytes })}`, { raw: true }),
  download: (run: string, profile: string, paths?: string[], stages?: string[]) =>
    request<{ job_id: string }>(`/runs/${enc(run)}/download`, post({ profile, paths, stages: stages?.length ? stages : null })),
  downloadPlans: (run: string, stages: string[]) => request<DownloadPlans>(`/runs/${enc(run)}/download/plans${stages.length ? "?" + stages.map((s) => `stages=${encodeURIComponent(s)}`).join("&") : ""}`),
  sequences: (run: string, opts: { table?: number | null; metric?: string | null; cls?: string | null; limit?: number }) =>
    request<SequencesResponse>(`/runs/${enc(run)}/sequences${q({ table: opts.table ?? undefined, metric: opts.metric, cls: opts.cls, limit: opts.limit })}`),
  exportMetrics: (run: string) => request<{ json: string; csv: string; root?: string; json_rel?: string; csv_rel?: string }>(`/runs/${enc(run)}/metrics/export`, { method: "POST" }),
  artifactUrl: (run: string, path: string) => `/api/v1/runs/${enc(run)}/artifacts/file${q({ path })}`,
  runDataset: (run: string, refresh = false) => request<DatasetInfo>(`/runs/${enc(run)}/dataset${refresh ? "?refresh=true" : ""}`),
  runCompute: (run: string, refresh = false) => request<ComputeInfo>(`/runs/${enc(run)}/compute${refresh ? "?refresh=true" : ""}`),
  precisionRecall: (run: string) => request<PrecisionRecall>(`/runs/${enc(run)}/precision-recall`),
  precisionRecallLogs: (run: string) => request<{ lines: PrLogLine[]; files: LogFileScan[]; notes: string[] }>(`/runs/${enc(run)}/precision-recall/logs`),
  evalFiles: (run: string) => request<EvalFile[]>(`/runs/${enc(run)}/eval-files`),
  preview: (run: string, path: string) => request<Preview>(`/runs/${enc(run)}/artifacts/preview${q({ path })}`),
  studyAddComparison: (id: string, runs: string[], note: string, stages?: Record<string, string>) => request<Study>(`/studies/${enc(id)}/comparisons`, { method: "POST", body: JSON.stringify({ runs, note, stages }) }),
  studyDeleteComparison: (id: string, cid: string) => request<Study>(`/studies/${enc(id)}/comparisons/${enc(cid)}`, { method: "DELETE" }),
  runCatalog: (run: string) => request<RunCatalog>(`/runs/${enc(run)}/catalog`),
  knowledge: () => request<{ entries: KnowledgeEntry[]; notes: string[]; folder: string; file: string }>("/knowledge"),
  knowledgeSave: (e: KnowledgeEntry) => request<KnowledgeEntry>(`/knowledge/${enc(e.id)}`, { method: "PUT", body: JSON.stringify(e) }),
  knowledgeDelete: (id: string) => request<unknown>(`/knowledge/${enc(id)}`, { method: "DELETE" }),
  knowledgeImport: (yaml: string) => request<{ added: string[]; errors: string[] }>("/knowledge/import", { method: "POST", body: JSON.stringify({ yaml, via: "ui-import" }) }),
  knowledgeSources: () => request<{ sources: KnowledgeSourceStatus[]; inbox: string }>("/knowledge/sources"),
  knowledgeReindex: () => request<{ reports: SourceReport[]; sources: KnowledgeSourceStatus[] }>("/knowledge/sources/refresh", { method: "POST" }),
  docsSearch: (query: string, source?: string) => request<{ hits: DocHit[] }>(`/docs/search${q({ q: query, source, limit: 40 })}`),
  docsFiles: (source?: string) => request<{ files: DocFile[] }>(`/docs/files${q({ source, limit: 200 })}`),
  docsFile: (source: string, path: string) => request<{ source: string; path: string; title: string; text: string; codebase: string }>(`/docs/file${q({ source, path })}`),
  beliefs: () => request<{ beliefs: BeliefItem[] }>("/beliefs"),
  belief: (id: string) => request<BeliefDetail>(`/beliefs/${enc(id)}`),
  beliefVocabulary: (scope: string) => request<BeliefVocabulary>(`/beliefs/vocabulary${q({ scope })}`),
  verifyBelief: (id: string) => request<{ job_id: string }>(`/beliefs/${enc(id)}/verify`, { method: "POST" }),
  verifyAllBeliefs: () => request<{ job_id: string }>("/beliefs/verify-all", { method: "POST" }),
  harnessTasks: () => request<Record<string, string>>("/harness/tasks"),
  harnessBriefUrl: (run: string, task: string) => `/api/v1/runs/${enc(run)}/harness-brief${q({ task })}`,
  runKnowledge: (run: string) => request<Record<string, AppliedKnowledge>>(`/runs/${enc(run)}/knowledge`),
  compareCurves: (runs: string[], keys?: string[], stages?: Record<string, string>, stage?: string | null) => request<CurvesComparison>("/compare/curves", { method: "POST", body: JSON.stringify({ runs, keys, stages, stage: stage || undefined }) }),
  compareAsk: (runs: string[], question: string, keys?: string[]) =>
    request<ChartAnswer>("/compare/ask", { method: "POST", body: JSON.stringify({ runs, keys, question }) }),
  compare: (baseline: string, candidate: string, stage?: string | null) => request<Comparison>(`/compare${q({ baseline, candidate, stage })}`),
  analyses: () => request<AnalysisInfo[]>("/analyses"),
  analyze: (run: string, baseline?: string | null, analyses?: string[]) =>
    request<AnalysisResult[]>("/analyze", post({ run, baseline: baseline || null, analyses: analyses?.length ? analyses : null })),
  prompts: () => request<PromptTemplate[]>("/prompts"),
  investigate: (body: { run: string; baseline?: string | null; question?: string | null; prompt_id?: string | null; persona?: string; variables?: Record<string, string>; others?: string[] }) =>
    request<Investigation>("/investigate", post(body)),
  investigations: (run?: string) => request<InvestigationSummary[]>(`/investigations${q({ run })}`),
  investigation: (id: string) => request<Investigation>(`/investigations/${enc(id)}`),
  report: (body: { run: string; baseline?: string | null; investigation_id?: string | null; format: "markdown" | "html" }) =>
    request<string>("/reports", { ...post(body), raw: true }),
  sync: (backend?: string | null) => request<{ job_id: string }>(`/sync${q({ backend })}`, { method: "POST" }),
  jobs: () => request<Job[]>("/jobs"),
  job: (id: string) => request<Job>(`/jobs/${enc(id)}`),
  checkBackend: (name: string) => request<{ job_id: string }>(`/backends/${enc(name)}/check`, { method: "POST" }),
  healthDeep: () => request<DeepHealth>("/health/deep"),
  warmUp: (run: string, force = false) => request<{ job_id: string }>(`/runs/${enc(run)}/warm${q({ force: force || undefined })}`, { method: "POST" }),
  warmRuns: (runs: string[]) => request<Record<string, boolean>>(`/runs/warm${q({ runs })}`),
  prefetchTasks: () => request<{ tasks: PrefetchSummary[] }>("/prefetch"),
  prefetchPlan: (body: { run: string; profile?: string; stages?: string[]; paths?: string[] }) => request<PrefetchTask>("/prefetch/plan", post(body)),
  prefetchTask: (id: string) => request<PrefetchTask>(`/prefetch/${enc(id)}`),
  prefetchSelect: (id: string, body: { only?: string[]; keys?: string[]; selected?: boolean }) => request<PrefetchTask>(`/prefetch/${enc(id)}/selection`, { method: "PUT", body: JSON.stringify(body) }),
  prefetchAction: (id: string, action: "start" | "stop" | "cancel") => request<PrefetchTask>(`/prefetch/${enc(id)}/${action}`, { method: "POST" }),
  prefetchDelete: (id: string) => request<unknown>(`/prefetch/${enc(id)}`, { method: "DELETE" }),
  starExperiment: (name: string, value: boolean) => request<{ name: string; starred: boolean }>(`/experiments/star${q({ name, value })}`, { method: "PUT" }),
  experiments: (backend?: string | null) => request<{ name: string; runs: number; last: string | null; users: string[]; mine: boolean; starred: boolean }[]>(`/experiments${q({ backend: backend ?? undefined, project: activeProject ?? undefined })}`),
  rerunJob: (id: string) => request<{ job_id: string }>(`/jobs/${enc(id)}/rerun`, { method: "POST" }),
  cancelJob: (id: string) => request<unknown>(`/jobs/${enc(id)}`, { method: "DELETE" }),
  llmSettings: () => request<LLMSettings>("/settings/llm"),
  saveLlmSettings: (body: Partial<LLMSettings> & { api_key?: string | null }) =>
    request<LLMSettings>("/settings/llm", { method: "PUT", body: JSON.stringify(body) }),
  storage: () => request<StorageInfo>("/settings/storage"),
  validateStorage: (role: string, path: string) => request<StorageRole>("/settings/storage/validate", post({ role, path })),
  saveStorage: (body: Record<string, string | number | null>) => request<StorageInfo & { applied_now?: string[] }>("/settings/storage", { method: "PUT", body: JSON.stringify(body) }),
  timeline: (run: string) => request<RunTimeline>(`/runs/${enc(run)}/timeline`),
  codeDiff: (run: string, stage?: string | null) => request<CodeDiff>(`/runs/${enc(run)}/code-diff${q({ stage: stage ?? undefined })}`),
  meNames: () => request<{ names: string[]; source: string; editable: boolean }>("/me/names"),
  setMeNames: (names: string[]) => request<{ names: string[]; source: string; editable: boolean }>("/me/names", { method: "PUT", body: JSON.stringify({ names }) }),
  pingLlm: () => request<LlmStatus>("/settings/llm/ping", { method: "POST" }),
  connectLocalLlm: (base_url?: string) => request<{ ok: boolean; needs_url?: boolean; base_url?: string; models?: string[]; model?: string; latency_ms?: number; error?: string }>("/settings/llm/connect-local", { method: "POST", body: JSON.stringify({ base_url }) }),
  testLlm: () => request<{ ok: boolean; model?: string; latency_ms: number; reply?: string | null; error?: string | null; checks?: { name: string; ok: boolean; latency_ms: number; error?: string }[]; notes?: string[] }>("/settings/llm/test", { method: "POST" }),
  diagnostics: () => request<Diagnostics>("/diagnostics"),
  askFinding: (id: string, question?: string) => request<Investigation>(`/findings/${enc(id)}/ask`, post({ question: question ?? "" })),
  explainFinding: (id: string) => request<{ why: string[]; would_change: string[] }>(`/findings/${enc(id)}/explain`),
  findingFeedback: (id: string, verdict: "wrong" | "correct" | "unsure", note = "") =>
    request<{ verdict: string }>(`/findings/${enc(id)}/feedback`, { method: "PUT", body: JSON.stringify({ verdict, note }) }),
  feedbackReport: () => request<{ total: number; analyses: { analysis: string; wrong: number; correct: number; unsure: number; false_alarm_rate: number | null; examples: { title: string; note: string }[] }[] }>("/feedback/report"),
  nextExperiment: (id: string) => request<NextExperiment>(`/findings/${enc(id)}/next-experiment`, post({})),
  me: () => request<{ id: string; name: string; admin: boolean; mode: string }>("/me"),
  check: (run?: string | null) => request<CheckResult>(`/check${q({ run })}`),
  glossary: () => request<Record<string, string>>("/glossary"),
  teamSettings: () => request<TeamPolicy>("/settings/team"),
  azureSettings: () => request<AzureSettings>("/settings/azure"),
  metricNames: (baseline: string, candidate: string) => request<MetricNamesDto>(`/compare/metric-names${q({ baseline, candidate })}`),
  saveMetricAliases: (body: { add?: Record<string, string>; remove?: string[]; runs?: string[] }) => request<{ aliases: Record<string, string>; path: string }>("/settings/metric-aliases", { method: "PUT", body: JSON.stringify(body) }),
  localRuns: () => request<LocalRunsState>("/settings/local-runs"),
  localRunsPreview: (path: string) => request<LocalPreview>("/settings/local-runs/preview", { method: "POST", body: JSON.stringify({ path }) }),
  localBrowse: (path: string, hidden = false) => request<FolderListing>(`/settings/local-runs/browse${q({ path, hidden: hidden || undefined })}`),
  localComplete: (prefix: string) => request<{ prefix: string; folders: string[] }>(`/settings/local-runs/complete${q({ prefix })}`),
  localRunsAdd: (path: string) => request<LocalRunsState & { added: LocalPreview }>("/settings/local-runs/add", { method: "POST", body: JSON.stringify({ path }) }),
  saveLocalRuns: (folders: string[]) => request<LocalRunsState>("/settings/local-runs", { method: "PUT", body: JSON.stringify({ folders }) }),
  azureAvailable: (subscription_id?: string | null) => request<AzureAvailable>(`/settings/azure/available${q({ subscription_id })}`),
  detectAzure: (subscription_id?: string | null) => request<AzureDetected>(`/settings/azure/detect${q({ subscription_id })}`, { method: "POST" }),
  saveAzure: (body: { defaults: Partial<AzureDefaultsCfg>; workspaces: AzureWorkspaceCfg[] }) =>
    request<AzureSettings>("/settings/azure", { method: "PUT", body: JSON.stringify(body) }),

  // ---- studies
  studies: () => request<StudySummary[]>("/studies"),
  studiesPage: async (params: { status?: string; q?: string; limit?: number; offset?: number }) => {
    const res = await fetch(`/api/v1/studies${q(params)}`);
    if (!res.ok) throw new ApiError(res.status, "http_error", `${res.status} ${res.statusText}`);
    return { items: (await res.json()) as StudySummary[], total: Number(res.headers.get("X-Total-Count") ?? 0) };
  },
  drafts: (kind?: string) => request<{ drafts: DraftSummary[]; total: number }>(`/drafts${q({ kind })}`),
  draft: <T,>(id: string) => request<DraftSummary & { payload: T }>(`/drafts/${enc(id)}`),
  saveDraft: (id: string, body: { kind: string; title: string; route: string; payload: unknown }) => request<DraftSummary>(`/drafts/${enc(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteDraft: (id: string) => request<unknown>(`/drafts/${enc(id)}`, { method: "DELETE" }),
  work: () => request<WorkInProgress>("/work"),
  createStudy: (body: { title: string; goal?: string; runs?: string[]; baseline?: string | null }) => request<Study>("/studies", post(body)),
  study: (id: string) => request<Study>(`/studies/${enc(id)}`),
  studyTemplates: () => request<QuestionTemplate[]>("/study-templates"),
  studyWizard: (body: { template: string; title?: string; runs: string[]; baseline?: string | null; claim?: string; deciding_metric?: string; falsified_if?: string; confounders?: string[] }) =>
    request<Study>("/studies/wizard", post(body)),
  studyGate: (id: string) => request<Study>(`/studies/${enc(id)}/gate`, { method: "POST" }),
  studyConfounder: (id: string, confounder: string, checked: boolean) =>
    request<Study>(`/studies/${enc(id)}/confounders`, { method: "PUT", body: JSON.stringify({ confounder, checked }) }),
  studyDecide: (id: string, body: { outcome: DecisionOutcome; decision: string; rationale?: string; based_on?: string[]; gate_override?: string }) =>
    request<Study>(`/studies/${enc(id)}/decision`, { method: "PUT", body: JSON.stringify(body) }),
  studyEvidence: (id: string) => request<StudyEvidence>(`/studies/${enc(id)}/evidence`),
  patchStudy: (id: string, body: { title?: string; goal?: string; baseline?: string; status?: "open" | "closed" }) =>
    request<Study>(`/studies/${enc(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteStudy: (id: string) => request<unknown>(`/studies/${enc(id)}`, { method: "DELETE" }),
  studyAddRun: (id: string, run: string) => request<Study>(`/studies/${enc(id)}/runs`, post({ run })),
  studyRemoveRun: (id: string, run: string) => request<Study>(`/studies/${enc(id)}/runs/${enc(run)}`, { method: "DELETE" }),
  studyAddNote: (id: string, text: string) => request<Study>(`/studies/${enc(id)}/notes`, post({ text })),
  studyDeleteNote: (id: string, nid: string) => request<Study>(`/studies/${enc(id)}/notes/${enc(nid)}`, { method: "DELETE" }),
  studyPin: (id: string, fid: string, pinned = true) => request<Study>(`/studies/${enc(id)}/pins/${enc(fid)}${q({ pinned })}`, { method: "PUT" }),
  studyAttachInvestigation: (id: string, investigation_id: string) => request<Study>(`/studies/${enc(id)}/investigations`, post({ investigation_id })),
  studyAddHypothesis: (id: string, body: { statement: string; rationale?: string; proposed_change?: string; evidence?: Omit<EvidenceLink, "supports">[] & Partial<EvidenceLink>[]; source?: string; reference_run?: string | null }) =>
    request<Study>(`/studies/${enc(id)}/hypotheses`, post(body)),
  studyPatchHypothesis: (id: string, hid: string, body: { statement?: string; status?: HypothesisStatus; rationale?: string; proposed_change?: string; verdict?: string; test_run?: string; reference_run?: string }) =>
    request<Study>(`/studies/${enc(id)}/hypotheses/${enc(hid)}`, { method: "PATCH", body: JSON.stringify(body) }),
  studyDeleteHypothesis: (id: string, hid: string) => request<Study>(`/studies/${enc(id)}/hypotheses/${enc(hid)}`, { method: "DELETE" }),
  studyAddEvidence: (id: string, hid: string, link: Partial<EvidenceLink> & { kind: EvidenceLink["kind"]; ref: string; label: string }) =>
    request<Study>(`/studies/${enc(id)}/hypotheses/${enc(hid)}/evidence`, post(link)),
  studyRemoveEvidence: (id: string, hid: string, kind: string, ref: string) =>
    request<Study>(`/studies/${enc(id)}/hypotheses/${enc(hid)}/evidence${q({ kind, ref })}`, { method: "DELETE" }),
  studyVerify: (id: string, hid: string) => request<VerifyResult>(`/studies/${enc(id)}/hypotheses/${enc(hid)}/verify`),
  studyExportUrl: (id: string) => `/api/v1/studies/${enc(id)}/export`,
  studyBookUrl: (id: string) => `/api/v1/studies/${enc(id)}/book`,
  buildStudyBook: (id: string, ai: boolean) => request<{ job_id: string }>(`/studies/${enc(id)}/book${q({ ai: ai || undefined })}`, { method: "POST" }),
};

export const refStr = (r: { backend: string; run_id: string }): string => `${r.backend}:${r.run_id}`;
