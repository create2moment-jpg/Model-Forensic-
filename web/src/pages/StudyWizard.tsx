import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, Loader2, Wand2, X } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { newDraftId, takeLocalDraft, useServerDraft } from "../lib/drafts";
import { DraftBadge } from "./Studies";
import { api } from "../api/client";
import type { QuestionTemplate } from "../api/types";
import { useApp } from "../components/AppState";
import { RunPicker } from "../components/RunPicker";
import { Card, ErrorBox, Loading } from "../components/ui";

const STEPS = ["Question", "Runs", "Pre-register", "Create"] as const;
interface WizardState { step: number; t: QuestionTemplate | null; run: string | null; baseline: string | null; metric: string; claim: string; falsifier: string; conf: string[]; title: string }
const EMPTY: WizardState = { step: 0, t: null, run: null, baseline: null, metric: "", claim: "", falsifier: "", conf: [], title: "" };
const WIZARD_KEYS = ["mf.wizard.step", "mf.wizard.template", "mf.wizard.run", "mf.wizard.baseline", "mf.wizard.metric", "mf.wizard.claim", "mf.wizard.falsifier", "mf.wizard.confounders", "mf.wizard.title"];

/** Guided study: pick a question, the runs, write the claim / deciding metric / falsifier before looking. */
export function StudyWizardPage() {
  const nav = useNavigate();
  const { toast } = useApp();
  const tpl = useQuery({ queryKey: ["study-templates"], queryFn: api.studyTemplates });
  // Every answer is a server draft (?draft=<id>): leave, restart, switch browser — Continue picks it up.
  const [sp, setSp] = useSearchParams();
  const qc = useQueryClient();
  const draftId = sp.get("draft");
  useEffect(() => {
    if (draftId) return;
    const old = takeLocalDraft(WIZARD_KEYS, (read) => {
      const tt = read("mf.wizard.template") as QuestionTemplate | null | undefined;
      return tt ? { ...EMPTY, step: Number(read("mf.wizard.step") ?? 0), t: tt, run: (read("mf.wizard.run") as string | null) ?? null,
        baseline: (read("mf.wizard.baseline") as string | null) ?? null, metric: String(read("mf.wizard.metric") ?? ""), claim: String(read("mf.wizard.claim") ?? ""),
        falsifier: String(read("mf.wizard.falsifier") ?? ""), conf: (read("mf.wizard.confounders") as string[] | undefined) ?? [], title: String(read("mf.wizard.title") ?? "") } : null;
    });
    const id = newDraftId("study-wizard");
    if (old) void api.saveDraft(id, { kind: "study-wizard", title: old.title || "Guided study", route: `/study-wizard?draft=${encodeURIComponent(id)}`, payload: old }).then(() => setSp({ draft: id }, { replace: true }));
    else setSp({ draft: id }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId]);
  const d = useServerDraft<WizardState>(draftId, "study-wizard", EMPTY,
    (v) => ({ title: v.title || v.t?.title || "Guided study", route: `/study-wizard?draft=${encodeURIComponent(draftId ?? "")}` }), (v) => !v.t);
  const { step, t, run, baseline, metric, claim, falsifier, conf, title } = d.value;
  const upd = (p: Partial<WizardState>): void => d.set({ ...d.value, ...p });
  const setStep = (v: number): void => upd({ step: v });
  const setRun = (v: string | null): void => upd({ run: v });
  const setBaseline = (v: string | null): void => upd({ baseline: v });
  const setMetric = (v: string): void => upd({ metric: v });
  const setClaim = (v: string): void => upd({ claim: v });
  const setFalsifier = (v: string): void => upd({ falsifier: v });
  const setConf = (v: string[]): void => upd({ conf: v });
  const setTitle = (v: string): void => upd({ title: v });
  const fill = (s: string): string => s.replaceAll("{candidate}", run ?? "<run>").replaceAll("{baseline}", baseline ?? "<baseline>").replaceAll("{metric}", metric || t?.default_metric || "<metric>");
  const pick = (x: QuestionTemplate): void => upd({ t: x, metric: x.default_metric, claim: "", falsifier: "", conf: x.confounders, title: x.title, step: 1 });
  const create = useMutation({
    mutationFn: () => api.studyWizard({ template: t!.id, title, runs: run ? [run] : [], baseline, claim: claim || undefined, deciding_metric: metric, falsified_if: falsifier || undefined, confounders: conf }),
    onSuccess: async (s) => { await d.discard(); void qc.invalidateQueries({ queryKey: ["studies-page"] }); toast("Study created — run the checks first", "good"); nav(`/studies/${encodeURIComponent(s.id)}?tab=decide`); },
  });
  const canNext = useMemo(() => [!!t, !!run && (!t?.needs_baseline || !!baseline), !!metric.trim(), true][step], [step, t, run, baseline, metric]);
  if (tpl.isLoading || !d.ready) return <div className="page"><Loading /></div>;
  return (
    <div className="page narrow" style={{ maxWidth: 980 }}>
      <div className="page-head"><div><h1>Guided study</h1>
        <div className="sub">Decide what would convince you <i>before</i> looking at the results — then check that the numbers can be trusted, then decide.</div></div></div>
      <div className="row" style={{ gap: 6, marginBottom: 14 }}>
        {STEPS.map((s, i) => <span key={s} className={`pill ${i === step ? "accent" : i < step ? "good" : "outline"}`}>{i < step ? <Check size={11} /> : null} {i + 1}. {s}</span>)}
        <span className="spacer" /><DraftBadge status={d.status} savedAt={d.savedAt} />
      </div>
      {step === 0 && (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
          {(tpl.data ?? []).map((x) => (
            <button key={x.id} className={`card wizard-tpl ${t?.id === x.id ? "selected" : ""}`} onClick={() => pick(x)}>
              <b>{x.title}</b><div className="muted" style={{ fontSize: 13, marginTop: 4 }}>{x.question.replace(/\{(candidate|baseline|metric)\}/g, (m) => ({ "{candidate}": "the run", "{baseline}": "the baseline", "{metric}": "the metric" })[m] ?? m)}</div>
              <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>{x.confounders.length} things to rule out first{x.needs_baseline ? " · needs a baseline" : ""}</div>
            </button>
          ))}
        </div>
      )}
      {step === 1 && t && (
        <Card title={t.title} sub={t.hint || "Pick the run you want to study and, if needed, the run to compare with."}>
          <div className="grid g2">
            <label className="field">Run under study<RunPicker value={run} onChange={setRun} width={400} /></label>
            <label className="field">Baseline {t.needs_baseline ? "(required)" : "(optional)"}<RunPicker value={baseline} onChange={setBaseline} width={400} /></label>
          </div>
        </Card>
      )}
      {step === 2 && t && (
        <Card title="Pre-register" sub="Written now, before you look at the results, so the verdict cannot be fitted to them afterwards.">
          <div className="col" style={{ gap: 10 }}>
            <label className="field">Study title<input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
            <label className="field">Claim<input className="input" value={claim || fill(t.claim)} onChange={(e) => setClaim(e.target.value)} /></label>
            <label className="field">Deciding metric (and its ruler is recorded from the baseline config)<input className="input mono" value={metric} onChange={(e) => setMetric(e.target.value)} /></label>
            <label className="field">Falsified if<input className="input" value={falsifier || fill(t.falsified_if)} onChange={(e) => setFalsifier(e.target.value)} /></label>
            <div><div className="faint">Confounders to rule out first</div>
              {t.confounders.map((c) => <label key={c} className="row" style={{ gap: 8, margin: "4px 0" }}><input type="checkbox" checked={conf.includes(c)} onChange={(e) => setConf(e.target.checked ? [...conf, c] : conf.filter((x) => x !== c))} /> {c}</label>)}
            </div>
          </div>
        </Card>
      )}
      {step === 3 && t && (
        <Card title="Ready">
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
            <li><b>{title}</b> — {t.title}</li><li>Run: <span className="mono">{run}</span>{baseline && <> vs baseline <span className="mono">{baseline}</span></>}</li>
            <li>Claim: {claim || fill(t.claim)}</li><li>Decided by <span className="mono">{metric}</span>; falsified if: {falsifier || fill(t.falsified_if)}</li>
            <li>Analyses: {t.analyses.join(", ")}</li>
          </ul>
          <div className="faint" style={{ marginTop: 8 }}>Next: the study opens on <b>Decide</b> — run the checks P1–P8 first; failing measurement checks block a verdict.</div>
          {create.error && <ErrorBox error={create.error} />}
        </Card>
      )}
      <div className="row" style={{ marginTop: 14 }}>
        <button className="btn" disabled={step === 0} onClick={() => setStep(step - 1)}><ArrowLeft size={14} /> Back</button>
        <button className="btn ghost" onClick={() => { if (!t || confirm("Cancel this guided study? Your answers are discarded.")) { void d.discard().then(() => nav("/studies")); } }}><X size={14} /> Cancel</button>
        <span className="spacer" />
        {step < 3 ? <button className="btn primary" disabled={!canNext} onClick={() => setStep(step + 1)}>Next <ArrowRight size={14} /></button>
          : <button className="btn primary" disabled={create.isPending} onClick={() => create.mutate()}>{create.isPending ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />} Create study</button>}
      </div>
    </div>
  );
}
