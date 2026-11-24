import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, KeyRound, Loader2, PlugZap, Server, ShieldCheck, Users, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LLMSettings } from "../api/types";
import { useApp } from "../components/AppState";
import { AzureWorkspaces } from "../components/AzureWorkspaces";
import { LocalRunsSettings } from "../components/LocalRuns";
import { StorageSettings } from "../components/StorageSettings";
import { MeSettings } from "../components/MeSettings";
import { FeatureSettings } from "../components/FeatureSettings";
import { NavSettings } from "../components/NavSettings";
import { ThresholdSettings } from "../components/ThresholdSettings";
import { Card, ErrorBox, Loading, Pill, Switch } from "../components/ui";

const PRESETS: { id: string; label: string; base_url: string; model: string; azure?: boolean; keyless?: boolean }[] = [
  { id: "openai", label: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { id: "azure", label: "Azure OpenAI", base_url: "https://<resource>.openai.azure.com/openai/deployments/<deployment>", model: "<deployment>", azure: true },
  { id: "vllm", label: "vLLM", base_url: "http://localhost:8000/v1", model: "meta-llama/Llama-3.1-70B-Instruct", keyless: true },
  { id: "ollama", label: "Ollama", base_url: "http://localhost:11434/v1", model: "llama3.1", keyless: true },
  { id: "custom", label: "Other OpenAI-compatible", base_url: "", model: "" },
];

export function SettingsPage() {
  const qc = useQueryClient();
  const { toast } = useApp();
  const s = useQuery({ queryKey: ["llm-settings"], queryFn: api.llmSettings });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const [form, setForm] = useState<LLMSettings | null>(null);
  const [key, setKey] = useState("");
  useEffect(() => { if (s.data && !form) setForm(s.data); }, [s.data, form]);

  const save = useMutation({
    mutationFn: (clearKey?: boolean) => api.saveLlmSettings({ ...form!, api_key: clearKey ? "" : key ? key : null }),
    onSuccess: (d) => { setForm(d); setKey(""); void qc.invalidateQueries({ queryKey: ["health"] }); void qc.invalidateQueries({ queryKey: ["llm-settings"] }); toast("Provider settings applied", "good"); },
  });
  const test = useMutation({ mutationFn: api.testLlm });

  if (s.isLoading || !form) return <div className="page"><Loading /></div>;
  const upd = <K extends keyof LLMSettings>(k: K, v: LLMSettings[K]): void => setForm({ ...form, [k]: v });
  const preset = (p: (typeof PRESETS)[number]): void => setForm({ ...form, enabled: true, base_url: p.base_url, model: p.model, azure_api_version: p.azure ? form.azure_api_version ?? "2024-06-01" : null });
  const keyStatus = form.has_session_key ? <Pill tone="good">session key set</Pill> : form.has_env_key ? <Pill tone="good">key from {form.api_key_env}</Pill> : <Pill tone="outline">no key</Pill>;

  return (
    <div className="page" style={{ maxWidth: 1100 }}>
      <div className="page-head"><div><h1>Settings</h1><div className="sub">Your Azure ML workspaces, and bring-your-own-key LLM: any OpenAI-compatible endpoint — OpenAI, Azure OpenAI, vLLM, Ollama, internal gateways.</div></div></div>
      <div className="col" style={{ gap: 14 }}>
        <NavSettings />
        <FeatureSettings />
        <ThresholdSettings />
        <AzureWorkspaces />
        <LocalRunsSettings />
        <MeSettings />
        <StorageSettings />
        <TeamPolicyCard />
        <Card title={<span className="row"><PlugZap size={15} color="var(--ai)" /> LLM provider</span>} right={<><span className="muted">Enabled</span><Switch on={form.enabled} onChange={(v) => upd("enabled", v)} /></>}>
          <div className="col" style={{ gap: 16 }}>
            <div className="row wrap" style={{ gap: 6 }}>{PRESETS.map((p) => <button key={p.id} className="btn sm" onClick={() => preset(p)}>{p.label}</button>)}</div>
            <div className="grid g2">
              <label className="field">Endpoint (base URL)<input className="input mono" value={form.base_url} onChange={(e) => upd("base_url", e.target.value)} /><span className="hint">…/v1 — requests go to /chat/completions</span></label>
              <label className="field">Model / deployment<input className="input mono" value={form.model} onChange={(e) => upd("model", e.target.value)} /></label>
              <label className="field">
                <span className="row">API key {keyStatus}</span>
                <div className="row">
                  <div className="input-wrap" style={{ flex: 1 }}><KeyRound size={13} /><input className="input mono" type="password" autoComplete="off" placeholder={form.has_session_key ? "•••••••• (unchanged)" : "sk-… (leave empty for keyless local servers)"} value={key} onChange={(e) => setKey(e.target.value)} /></div>
                  {form.has_session_key && <button className="btn sm danger" onClick={() => save.mutate(true)}>Clear</button>}
                </div>
                <span className="hint">Held in server memory for this session only — never written to disk, never returned by the API, redacted from logs. For persistence set the env var below.</span>
              </label>
              <label className="field">API key environment variable<input className="input mono" value={form.api_key_env} onChange={(e) => upd("api_key_env", e.target.value)} /><span className="hint">Used when no session key is set.</span></label>
              <label className="field">Azure OpenAI api-version<input className="input mono" placeholder="(not Azure)" value={form.azure_api_version ?? ""} onChange={(e) => upd("azure_api_version", e.target.value || null)} /></label>
              <label className="field">Reasoning effort<select className="select" value={form.reasoning_effort ?? ""} onChange={(e) => upd("reasoning_effort", e.target.value || null)}><option value="">(not sent)</option><option>low</option><option>medium</option><option>high</option></select><span className="hint">Only for servers/models that support it.</span></label>
            </div>
            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))" }}>
              <label className="field">Temperature <span className="mono">{form.temperature.toFixed(2)}</span><input className="range" style={{ width: "100%" }} type="range" min={0} max={1.5} step={0.05} value={form.temperature} onChange={(e) => upd("temperature", Number(e.target.value))} /></label>
              <label className="field">Max output tokens<input className="input" type="number" value={form.max_tokens} onChange={(e) => upd("max_tokens", Number(e.target.value))} /></label>
              <label className="field">Context limit (tokens)<input className="input" type="number" value={form.context_limit_tokens} onChange={(e) => upd("context_limit_tokens", Number(e.target.value))} /></label>
              <label className="field">Timeout (s)<input className="input" type="number" value={form.timeout_s} onChange={(e) => upd("timeout_s", Number(e.target.value))} /></label>
              <label className="field">Max tool steps<input className="input" type="number" min={0} max={10} value={form.max_tool_steps} onChange={(e) => upd("max_tool_steps", Number(e.target.value))} /><span className="hint">Bounded agent loop.</span></label>
            </div>
            <div className="row">
              <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate(false)}>{save.isPending && <Loader2 size={14} className="spin" />} Apply</button>
              <button className="btn" disabled={test.isPending} onClick={() => test.mutate()}>{test.isPending ? <Loader2 size={14} className="spin" /> : <PlugZap size={14} />} Test connection</button>
              <span className="spacer" />
              <span className="faint">Active: <span className="mono">{s.data?.active_model}</span></span>
            </div>
            {save.error && <ErrorBox error={save.error} />}
            {test.data && (test.data.ok
              ? <div className="note"><CheckCircle2 size={15} color="var(--good)" /> Connected to <span className="mono">{test.data.model}</span> in {test.data.latency_ms} ms — replied “{test.data.reply}”.</div>
              : <div className="error-box"><XCircle size={16} color="var(--bad)" /><div><b>Connection failed</b><div className="muted mono">{test.data.error}</div></div></div>)}
            {test.data?.checks && test.data.checks.length > 0 && (
              <div className="llm-checks">
                {test.data.checks.map((c) => (
                  <div key={c.name} className="row" style={{ gap: 6 }}>
                    {c.ok ? <CheckCircle2 size={14} color="var(--good)" /> : <XCircle size={14} color="var(--bad)" />}
                    <span className="mono" style={{ width: 90 }}>{c.name.replace("_", " ")}</span>
                    <span className="faint">{c.latency_ms} ms</span>
                    {c.error && <span className="faint mono ellipsis" title={c.error}>{c.error}</span>}
                  </div>
                ))}
                {test.data.notes?.map((n) => <div key={n} className="faint">ⓘ {n}</div>)}
              </div>
            )}
          </div>
        </Card>
        <Card title={<span className="row"><ShieldCheck size={15} color="var(--good)" /> Safety guarantees</span>}>
          <ul className="muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
            <li>Deterministic analyses run first; the model receives structured, cited evidence — never a raw dump.</li>
            <li>Logs, configs and artifact text are fenced as untrusted data; instructions inside them are ignored.</li>
            <li>The AI can only call read-only tools; downloads require your confirmation; nothing can mutate your workspace.</li>
            <li>Every claim must cite evidence; numbers not present in the evidence are flagged as unverified.</li>
          </ul>
        </Card>
        <Card title={<span className="row"><Server size={15} /> Backends</span>}>
          <div className="row wrap" style={{ gap: 6 }}>
            {health.data?.backends.map((b) => <Pill key={b} tone="accent">{b}</Pill>)}
            {health.data?.read_only && <Pill tone="good">read-only mode</Pill>}
          </div>
          <div className="faint" style={{ marginTop: 10 }}>Azure workspaces come from <code>config/azure.local.yaml</code> (above); local exports from <code>model-forensic.yaml</code>. Run <code>mf doctor --live</code> to check access and speed.</div>
        </Card>
      </div>
    </div>
  );
}

function TeamPolicyCard() {
  const t = useQuery({ queryKey: ["team-policy"], queryFn: api.teamSettings });
  if (!t.data) return null;
  const p = t.data;
  return (
    <Card title={<span className="row"><Users size={15} /> Team policy</span>} right={<span className="faint mono">{p.source ?? `${p.path} (not created)`}</span>}>
      <div className="col" style={{ gap: 10 }}>
        <div className="faint">Edit <code>{p.path}</code> (git-ignored; template <code>config/team.example.yaml</code>) and restart. It sets de-prioritised classes, the metric families each stage should log, where metrics are computed (cited by the AI) and default analysis parameters.</div>
        <div className="row wrap" style={{ gap: 6 }}><span className="faint">De-prioritised classes</span>{p.deprioritized_classes.length ? p.deprioritized_classes.map((c) => <Pill key={c} tone="outline">{c}</Pill>) : <span className="faint">none</span>}</div>
        <div className="row wrap" style={{ gap: 6 }}><span className="faint">Expected metric families</span>{Object.entries(p.expected_metrics).map(([k, v]) => <Pill key={k} tone="accent">{k}: {Object.keys(v).length}</Pill>)}{!Object.keys(p.expected_metrics).length && <span className="faint">default (train/loss)</span>}</div>
        <div className="row wrap" style={{ gap: 6 }}><span className="faint">Code references</span><span>{Object.keys(p.code_refs).length}</span><span className="faint">Analysis defaults</span><span>{Object.keys(p.analysis_params).join(", ") || "none"}</span></div>
      </div>
    </Card>
  );
}
