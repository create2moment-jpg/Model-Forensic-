import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Bot, CheckCircle2, Loader2, Sparkles, Wrench, X, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Claim, Investigation, PromptTemplate } from "../api/types";
import { evidenceSummary, isMultiRun, renderPreview, VARIABLE_HELP } from "../lib/prompts";
import { PromptPicker } from "./PromptPicker";
import { fmtAgo } from "../lib/format";
import { RunPicker } from "./RunPicker";
import { Card, Empty, ErrorBox, Pill } from "./ui";

const PERSONAS = [
  { id: "analyst", label: "Forensic analyst" },
  { id: "skeptical_reviewer", label: "Skeptical reviewer" },
  { id: "cvpr_reviewer", label: "CVPR reviewer" },
  { id: "ad_perception_engineer", label: "Senior AD perception engineer" },
  { id: "disprover", label: "Try to disprove the diagnosis" },
];
const CLAIM_TONE = { FACT: "outline", INFERENCE: "info", HYPOTHESIS: "warn", RECOMMENDATION: "good" } as const;

const SUGGESTED = {
  single: ["study_health_check", "explain_run", "study_eval_breakdown", "training_dynamics", "study_config_sanity", "logs_triage"],
  baseline: ["cmp_what_changed_and_effect", "why_worse", "cmp_is_it_real", "cmp_slices", "cmp_camera_backbone", "cmp_radar_multiscale"],
  multi: ["cmp_multi_rank", "cmp_multi_users", "cmp_multi_sweep", "cmp_best_recipe"],
};

export function InvestigationPanel({ run, baseline, initialId, onRunChange, onBaselineChange, others = [], onOthersChange, onInvestigated, claimAction }: {
  run: string | null; baseline: string | null; initialId?: string | null;
  onRunChange?: (v: string) => void; onBaselineChange?: (v: string | null) => void;
  others?: string[]; onOthersChange?: (v: string[]) => void;
  /** Called after each new investigation (e.g. to attach it to a study). */
  onInvestigated?: (inv: Investigation) => void;
  /** Extra action per claim (e.g. "→ hypothesis"). */
  claimAction?: (claim: Claim, inv: Investigation) => React.ReactNode;
}) {
  const qc = useQueryClient();
  const prompts = useQuery({ queryKey: ["prompts"], queryFn: api.prompts });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const history = useQuery({ queryKey: ["investigations", run], queryFn: () => api.investigations(run ?? undefined) });
  const [question, setQuestion] = useState("");
  const [promptId, setPromptId] = useState<string | null>(null);
  const [vars, setVars] = useState<Record<string, string>>({});
  const [persona, setPersona] = useState("analyst");
  const [current, setCurrent] = useState<Investigation | null>(null);
  const [focusEv, setFocusEv] = useState<string | null>(null);

  useEffect(() => {
    if (initialId) void api.investigation(initialId).then(setCurrent);
  }, [initialId]);

  const all = prompts.data ?? [];
  const tpl = all.find((p) => p.id === promptId) ?? null;
  const missing = tpl ? tpl.requires.filter((v) => !["run_id", "baseline_run_id", "run_ids"].includes(v) && !vars[v]?.trim()) : [];
  const runIds = [run, baseline, ...others].filter(Boolean).join(", ");
  const preview = tpl ? renderPreview(tpl.template, { ...vars, run_id: run ?? "", baseline_run_id: baseline ?? "", run_ids: runIds }) : "";
  const suggested = (others.length ? SUGGESTED.multi : baseline ? SUGGESTED.baseline : SUGGESTED.single)
    .map((id) => all.find((p) => p.id === id)).filter((p): p is PromptTemplate => !!p);

  const m = useMutation({
    mutationFn: () => api.investigate({
      run: run!, baseline, question: promptId ? null : question || null, prompt_id: promptId, persona,
      variables: Object.fromEntries(Object.entries(vars).filter(([, v]) => v.trim())), others,
    }),
    onSuccess: (inv) => { setCurrent(inv); onInvestigated?.(inv); void qc.invalidateQueries({ queryKey: ["investigations"] }); },
  });
  const offline = health.data?.llm.startsWith("offline");
  const choose = (id: string | null): void => { setPromptId(id); };

  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0,1fr) 340px" }}>
      <div className="col" style={{ gap: 14 }}>
        <Card title={<span className="row"><Sparkles size={15} color="var(--ai)" /> Ask about the evidence</span>}
          right={<Pill tone="ai">{health.data?.llm ?? "…"}</Pill>}>
          <div className="col" style={{ gap: 12 }}>
            {(onRunChange || onBaselineChange) && (
              <div className="row wrap">
                {onRunChange && <label className="field">Run<RunPicker value={run} onChange={onRunChange} /></label>}
                {onBaselineChange && <label className="field">Baseline (optional)<RunPicker value={baseline} onChange={(v) => onBaselineChange(v)} placeholder="No baseline" /></label>}
                {onOthersChange && <label className="field">Add runs to compare (optional, max 8)<RunPicker value={null} onChange={(v) => onOthersChange([...new Set([...others, v])].slice(0, 8))} placeholder="Add another run…" /></label>}
              </div>
            )}
            {others.length > 0 && (
              <div className="row wrap" style={{ gap: 6 }}>
                <span className="faint" style={{ fontSize: 12 }}>Also in scope:</span>
                {others.map((o) => (
                  <span key={o} className="tag row" style={{ gap: 4 }}>{o.split(":").slice(1).join(":") || o}
                    {onOthersChange && <X size={11} style={{ cursor: "pointer" }} onClick={() => onOthersChange(others.filter((x) => x !== o))} />}
                  </span>
                ))}
              </div>
            )}
            <PromptPicker prompts={all} value={promptId} onChange={choose} hasBaseline={!!baseline} hasOthers={others.length > 0} />
            {!tpl && suggested.length > 0 && (
              <div className="row wrap" style={{ gap: 6 }}>
                <span className="faint" style={{ fontSize: 12 }}>Suggested:</span>
                {suggested.map((p) => <button key={p.id} className="btn sm" disabled={p.needs_baseline && !baseline} onClick={() => choose(p.id)} title={p.description}>{p.title}</button>)}
              </div>
            )}
            {tpl ? (
              <div className="col" style={{ gap: 10 }}>
                <div className="faint" style={{ fontSize: 12 }}>
                  {tpl.description} <span className="mono">· {tpl.id}@{tpl.version}</span>
                </div>
                {tpl.variables.length > 0 && (
                  <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
                    {tpl.variables.map((v) => (
                      <label key={v} className="field" style={["change_description", "hypothesis", "notes"].includes(v) ? { gridColumn: "1 / -1" } : undefined}>
                        <span>{VARIABLE_HELP[v]?.label ?? v}{tpl.requires.includes(v) ? " *" : <span className="hint"> (optional)</span>}</span>
                        <input className="input" value={vars[v] ?? ""} placeholder={VARIABLE_HELP[v]?.placeholder ?? ""} onChange={(e) => setVars({ ...vars, [v]: e.target.value })} />
                      </label>
                    ))}
                  </div>
                )}
                <div className="note" style={{ whiteSpace: "pre-wrap", fontSize: 12.5, color: "var(--text-2)" }}>{preview}</div>
                {evidenceSummary(tpl).length > 0 && (
                  <div className="row wrap" style={{ gap: 6 }}>
                    <span className="faint" style={{ fontSize: 12 }}>Extra evidence sent to the model:</span>
                    {evidenceSummary(tpl).map((e) => <span key={e} className="tag">{e}</span>)}
                  </div>
                )}
                {isMultiRun(tpl) && others.length === 0 && (
                  <div className="note warn"><AlertTriangle size={14} style={{ marginTop: 2 }} />This template compares several runs — add runs above (or start from Progression → Ask AI).</div>
                )}
              </div>
            ) : (
              <textarea className="input" rows={3} value={question} onChange={(e) => setQuestion(e.target.value)}
                placeholder="Or ask your own question, e.g. “Is the long-range regression explained by the radar branch or by the temporal window?”" />
            )}
            <div className="row wrap">
              <select className="select" aria-label="Persona" value={persona} onChange={(e) => setPersona(e.target.value)}>
                {PERSONAS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
              <span className="faint" style={{ fontSize: 12, flex: 1, minWidth: 200 }}>
                {missing.length ? `Fill in: ${missing.map((v) => VARIABLE_HELP[v]?.label ?? v).join(", ")}` : "Deterministic analyses run first; the AI interprets and must cite their evidence."}
              </span>
              <button className="btn primary" disabled={!run || m.isPending || (!promptId && !question.trim()) || missing.length > 0} onClick={() => m.mutate()}>
                {m.isPending ? <Loader2 size={14} className="spin" /> : <Bot size={14} />} Investigate
              </button>
            </div>
            {offline && (
              <div className="note warn"><AlertTriangle size={14} style={{ marginTop: 2 }} />
                <span>No LLM configured — using the offline heuristic interpreter (restates deterministic findings). <Link to="/settings" style={{ color: "var(--accent-2)" }}>Connect your own model →</Link></span>
              </div>
            )}
          </div>
        </Card>
        {m.error && <ErrorBox error={m.error} />}
        {m.isPending && <Card><div className="row muted"><Loader2 size={15} className="spin" /> Running analyses, building cited context, asking the model…</div></Card>}
        {current && !m.isPending && <InvestigationResult inv={current} focusEv={focusEv} setFocusEv={setFocusEv} claimAction={claimAction} />}
        {!current && !m.isPending && <Card><Empty icon={<Bot size={26} />} title="No investigation yet">Pick a prompt or ask a question. Claims are labelled FACT / INFERENCE / HYPOTHESIS / RECOMMENDATION and verified against the evidence.</Empty></Card>}
      </div>
      <Card title="History" flush>
        {(history.data ?? []).length === 0 ? <div className="empty" style={{ padding: 20 }}>None</div> : history.data!.map((h) => (
          <div key={h.id} className="palette-item" style={{ display: "block", borderRadius: 0, borderBottom: "1px solid var(--line)" }}
            onClick={() => void api.investigation(h.id).then(setCurrent)}>
            <div className="ellipsis" style={{ color: "var(--text)", fontWeight: 500 }}>{h.question}</div>
            <div className="row faint" style={{ fontSize: 11.5 }}>{Math.round(h.verified_ratio * 100)}% verified · {h.persona}<span className="spacer" />{fmtAgo(h.created_at)}</div>
          </div>
        ))}
      </Card>
    </div>
  );
}

function InvestigationResult({ inv, focusEv, setFocusEv, claimAction }: { inv: Investigation; focusEv: string | null; setFocusEv: (v: string | null) => void; claimAction?: (claim: Claim, inv: Investigation) => React.ReactNode }) {
  const evById = Object.fromEntries(inv.evidence.map((e) => [e.id, e]));
  const verified = inv.claims.filter((c) => c.verified).length;
  return (
    <>
      <Card title="Answer" right={<><Pill tone={inv.verified_ratio === 1 ? "good" : inv.verified_ratio > 0.5 ? "warn" : "bad"}>{verified}/{inv.claims.length} claims verified</Pill><Pill tone="ai">AI-generated</Pill></>}>
        <div className="col" style={{ gap: 10 }}>
          <div className="faint" style={{ fontSize: 12 }}>Q: {inv.question}</div>
          {inv.insufficient_evidence && <div className="note warn"><AlertTriangle size={14} style={{ marginTop: 2 }} /> Insufficient evidence for a factual answer.</div>}
          {inv.parse_error && <div className="note warn">{inv.parse_error}</div>}
          <div className="note ai" style={{ color: "var(--text)" }}><Sparkles size={14} color="var(--ai)" style={{ marginTop: 2, flex: "none" }} />{inv.summary}</div>
          {inv.claims.map((c, i) => (
            <div key={i} className={`claim ${c.verified ? "" : "unverified"}`}>
              {c.verified ? <CheckCircle2 size={16} color="var(--good)" style={{ flex: "none", marginTop: 1 }} /> : <XCircle size={16} color="var(--warn)" style={{ flex: "none", marginTop: 1 }} />}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row wrap" style={{ gap: 6, marginBottom: 4 }}>
                  <Pill tone={CLAIM_TONE[c.type]} dashed={c.type === "HYPOTHESIS"}>{c.type.toLowerCase()}</Pill>
                  {c.confidence && <span className="faint" style={{ fontSize: 11.5 }}>confidence {c.confidence}</span>}
                  {!c.verified && <Pill tone="warn">unverified</Pill>}
                  {claimAction && <span style={{ marginLeft: "auto" }}>{claimAction(c, inv)}</span>}
                </div>
                <div>{c.text}</div>
                <div className="row wrap" style={{ gap: 4, marginTop: 6 }}>
                  {c.evidence.map((e) => <button key={e} className="chip" onClick={() => setFocusEv(focusEv === e ? null : e)}>{e}</button>)}
                </div>
                {c.problems.map((p) => <div key={p} className="faint" style={{ fontSize: 12, color: "var(--warn)" }}>⚠ {p}</div>)}
                {c.evidence.includes(focusEv ?? "") && evById[focusEv!] && (
                  <div className="ev" style={{ marginTop: 6 }}><span className="chip">{focusEv}</span><span className="mono" style={{ whiteSpace: "pre-wrap" }}>{evById[focusEv!]!.text}</span></div>
                )}
              </div>
            </div>
          ))}
        </div>
      </Card>
      <div className="grid g2">
        <Card title="Tool calls" sub={`${inv.tool_calls.length}`}>
          {inv.tool_calls.length === 0 ? <span className="faint">The model answered from the initial evidence.</span> : inv.tool_calls.map((t, i) => (
            <div key={i} className="row" style={{ padding: "4px 0" }}><Wrench size={13} className="faint" /><span className="mono">{t.tool}</span><span className="faint mono ellipsis">{JSON.stringify(t.args)}</span><span className="spacer" /><span className="chip">{t.evidence_id}</span></div>
          ))}
        </Card>
        <Card title="Provenance">
          <dl className="kv" style={{ fontSize: 12 }}>
            <dt>Model</dt><dd className="mono">{inv.model}</dd>
            <dt>Prompt</dt><dd className="mono">{inv.prompt ?? "ad-hoc"}</dd>
            <dt>Persona</dt><dd>{inv.persona}</dd>
            <dt>Context hash</dt><dd className="mono">{inv.context_hash}</dd>
            <dt>Evidence items</dt><dd>{inv.evidence.length}</dd>
            <dt>Tokens</dt><dd>{inv.usage.prompt_tokens} in · {inv.usage.completion_tokens} out</dd>
            <dt>Id</dt><dd className="mono">{inv.id}</dd>
          </dl>
        </Card>
      </div>
    </>
  );
}
