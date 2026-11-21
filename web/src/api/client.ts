import type {
  AnalysisInfo, AnalysisResult, Backend, EvidenceLink, HypothesisStatus, Run, Study, StudyEvidence, StudySummary, VerifyResult, Comparison, Diagnostics, Health, Investigation, InvestigationSummary, Job, LLMSettings,
  MetricSeriesDto, PromptTemplate, RunOverview, SearchResponse, AzureSettings, AzureDetected, AzureDefaultsCfg, AzureWorkspaceCfg, StageInfo, DownloadPlans, SequencesResponse, TeamPolicy, CheckResult, QuestionTemplate, DecisionOutcome, NextExperiment, DeepHealth,
} from "./types";

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

export const api = {
  health: () => request<Health>("/health"),
  search: (query: string, limit = 200, offset = 0, backend?: string | null) => request<SearchResponse>(`/runs${q({ q: query, limit, offset, backend })}`),
  backends: () => request<Backend[]>("/backends"),
  runInfo: (runs: string[]) => request<Run[]>(`/run-info${q({ runs })}`),
  metricKeys: (runs: string[]) => request<Record<string, number>>(`/metric-keys${q({ runs })}`),
  suggest: (prefix: string, field = "name") => request<string[]>(`/suggest${q({ prefix, field })}`),
  overview: (run: string, baseline?: string | null) => request<RunOverview>(`/runs/${enc(run)}${q({ baseline })}`),
  stages: (run: string) => request<StageInfo[]>(`/runs/${enc(run)}/stages`),
  favorite: (run: string, value: boolean) => request<unknown>(`/runs/${enc(run)}/favorite${q({ value })}`, { method: "PUT" }),
  metrics: (run: string, keys: string[] = [], maxPoints = 2000) =>
    request<MetricSeriesDto[]>(`/runs/${enc(run)}/metrics${q({ keys, max_points: maxPoints })}`),
  flatConfig: (run: string) => request<Record<string, unknown>>(`/runs/${enc(run)}/config?flat=true`),
  logs: (run: string, tailBytes = 400_000) => request<string>(`/runs/${enc(run)}/logs${q({ tail_bytes: tailBytes })}`, { raw: true }),
  download: (run: string, profile: string, paths?: string[], stages?: string[]) =>
    request<{ job_id: string }>(`/runs/${enc(run)}/download`, post({ profile, paths, stages: stages?.length ? stages : null })),
  downloadPlans: (run: string, stages: string[]) => request<DownloadPlans>(`/runs/${enc(run)}/download/plans${stages.length ? "?" + stages.map((s) => `stages=${encodeURIComponent(s)}`).join("&") : ""}`),
  sequences: (run: string, opts: { table?: number | null; metric?: string | null; cls?: string | null; limit?: number }) =>
    request<SequencesResponse>(`/runs/${enc(run)}/sequences${q({ table: opts.table ?? undefined, metric: opts.metric, cls: opts.cls, limit: opts.limit })}`),
  exportMetrics: (run: string) => request<{ json: string; csv: string }>(`/runs/${enc(run)}/metrics/export`, { method: "POST" }),
  artifactUrl: (run: string, path: string) => `/api/v1/runs/${enc(run)}/artifacts/file${q({ path })}`,
  compare: (baseline: string, candidate: string) => request<Comparison>(`/compare${q({ baseline, candidate })}`),
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
  cancelJob: (id: string) => request<unknown>(`/jobs/${enc(id)}`, { method: "DELETE" }),
  llmSettings: () => request<LLMSettings>("/settings/llm"),
  saveLlmSettings: (body: Partial<LLMSettings> & { api_key?: string | null }) =>
    request<LLMSettings>("/settings/llm", { method: "PUT", body: JSON.stringify(body) }),
  testLlm: () => request<{ ok: boolean; model?: string; latency_ms: number; reply?: string; error?: string }>("/settings/llm/test", { method: "POST" }),
  diagnostics: () => request<Diagnostics>("/diagnostics"),
  askFinding: (id: string, question?: string) => request<Investigation>(`/findings/${enc(id)}/ask`, post({ question: question ?? "" })),
  nextExperiment: (id: string) => request<NextExperiment>(`/findings/${enc(id)}/next-experiment`, post({})),
  me: () => request<{ id: string; name: string; admin: boolean; mode: string }>("/me"),
  check: (run?: string | null) => request<CheckResult>(`/check${q({ run })}`),
  glossary: () => request<Record<string, string>>("/glossary"),
  teamSettings: () => request<TeamPolicy>("/settings/team"),
  azureSettings: () => request<AzureSettings>("/settings/azure"),
  detectAzure: (subscription_id?: string | null) => request<AzureDetected>(`/settings/azure/detect${q({ subscription_id })}`, { method: "POST" }),
  saveAzure: (body: { defaults: Partial<AzureDefaultsCfg>; workspaces: AzureWorkspaceCfg[] }) =>
    request<AzureSettings>("/settings/azure", { method: "PUT", body: JSON.stringify(body) }),

  // ---- studies
  studies: () => request<StudySummary[]>("/studies"),
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
  patchStudy: (id: string, body: { title?: string; goal?: string; baseline?: string }) =>
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
