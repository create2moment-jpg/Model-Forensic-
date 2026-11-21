import type {
  AnalysisInfo, AnalysisResult, Backend, Run, Comparison, Diagnostics, Health, Investigation, InvestigationSummary, Job, LLMSettings,
  MetricSeriesDto, PromptTemplate, RunOverview, SearchResponse,
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
  favorite: (run: string, value: boolean) => request<unknown>(`/runs/${enc(run)}/favorite${q({ value })}`, { method: "PUT" }),
  metrics: (run: string, keys: string[] = [], maxPoints = 2000) =>
    request<MetricSeriesDto[]>(`/runs/${enc(run)}/metrics${q({ keys, max_points: maxPoints })}`),
  flatConfig: (run: string) => request<Record<string, unknown>>(`/runs/${enc(run)}/config?flat=true`),
  logs: (run: string, tailBytes = 400_000) => request<string>(`/runs/${enc(run)}/logs${q({ tail_bytes: tailBytes })}`, { raw: true }),
  download: (run: string, profile: string, paths?: string[]) =>
    request<{ job_id: string }>(`/runs/${enc(run)}/download`, post({ profile, paths })),
  artifactUrl: (run: string, path: string) => `/api/v1/runs/${enc(run)}/artifacts/file${q({ path })}`,
  compare: (baseline: string, candidate: string) => request<Comparison>(`/compare${q({ baseline, candidate })}`),
  analyses: () => request<AnalysisInfo[]>("/analyses"),
  analyze: (run: string, baseline?: string | null, analyses?: string[]) =>
    request<AnalysisResult[]>("/analyze", post({ run, baseline: baseline || null, analyses: analyses?.length ? analyses : null })),
  prompts: () => request<PromptTemplate[]>("/prompts"),
  investigate: (body: { run: string; baseline?: string | null; question?: string | null; prompt_id?: string | null; persona?: string }) =>
    request<Investigation>("/investigate", post(body)),
  investigations: (run?: string) => request<InvestigationSummary[]>(`/investigations${q({ run })}`),
  investigation: (id: string) => request<Investigation>(`/investigations/${enc(id)}`),
  report: (body: { run: string; baseline?: string | null; investigation_id?: string | null; format: "markdown" | "html" }) =>
    request<string>("/reports", { ...post(body), raw: true }),
  sync: (backend?: string | null) => request<{ job_id: string }>(`/sync${q({ backend })}`, { method: "POST" }),
  jobs: () => request<Job[]>("/jobs"),
  job: (id: string) => request<Job>(`/jobs/${enc(id)}`),
  cancelJob: (id: string) => request<unknown>(`/jobs/${enc(id)}`, { method: "DELETE" }),
  llmSettings: () => request<LLMSettings>("/settings/llm"),
  saveLlmSettings: (body: Partial<LLMSettings> & { api_key?: string | null }) =>
    request<LLMSettings>("/settings/llm", { method: "PUT", body: JSON.stringify(body) }),
  testLlm: () => request<{ ok: boolean; model?: string; latency_ms: number; reply?: string; error?: string }>("/settings/llm/test", { method: "POST" }),
  diagnostics: () => request<Diagnostics>("/diagnostics"),
};

export const refStr = (r: { backend: string; run_id: string }): string => `${r.backend}:${r.run_id}`;
