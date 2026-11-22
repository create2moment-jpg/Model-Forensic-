import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, Loader2, Wand2, X } from "lucide-react";
import { useMemo } from "react";
import { useLocalStorage } from "../lib/hooks";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { QuestionTemplate } from "../api/types";
import { useApp } from "../components/AppState";
import { RunPicker } from "../components/RunPicker";
import { Card, ErrorBox, Loading } from "../components/ui";

const STEPS = ["Question", "Runs", "Pre-register", "Create"] as const;

/** Guided study: pick a question, the runs, write the claim / deciding metric / falsifier before looking. */
export function StudyWizardPage() {
  const nav = useNavigate();
  const { toast } = useApp();
  const tpl = useQuery({ queryKey: ["study-templates"], queryFn: api.studyTemplates });
  // Every answer is kept in this browser until the study is created or cancelled (leaving the page keeps it).
  const [step, setStep] = useLocalStorage("mf.wizard.step", 0);
  const [t, setT] = useLocalStorage<QuestionTemplate | null>("mf.wizard.template", null);
  const [run, setRun] = useLocalStorage<string | null>("mf.wizard.run", null);
  const [baseline, setBaseline] = useLocalStorage<string | null>("mf.wizard.baseline", null);
  const [metric, setMetric] = useLocalStorage("mf.wizard.metric", "");
  const [claim, setClaim] = useLocalStorage("mf.wizard.claim", "");
  const [falsifier, setFalsifier] = useLocalStorage("mf.wizard.falsifier", "");
  const [conf, setConf] = useLocalStorage<string[]>("mf.wizard.confounders", []);
  const [title, setTitle] = useLocalStorage("mf.wizard.title", "");
  const reset = (): void => { setStep(0); setT(null); setRun(null); setBaseline(null); setMetric(""); setClaim(""); setFalsifier(""); setConf([]); setTitle(""); };
  const fill = (s: string): string => s.replaceAll("{candidate}", run ?? "<run>").replaceAll("{baseline}", baseline ?? "<baseline>").replaceAll("{metric}", metric || t?.default_metric || "<metric>");
  const pick = (x: QuestionTemplate): void => { setT(x); setMetric(x.default_metric); setClaim(""); setFalsifier(""); setConf(x.confounders); setTitle(x.title); setStep(1); };
  const create = useMutation({
    mutationFn: () => api.studyWizard({ template: t!.id, title, runs: run ? [run] : [], baseline, claim: claim || undefined, deciding_metric: metric, falsified_if: falsifier || undefined, confounders: conf }),
    onSuccess: (s) => { reset(); toast("Study created — run the checks first", "good"); nav(`/studies/${encodeURIComponent(s.id)}?tab=decide`); },
  });
  const canNext = useMemo(() => [!!t, !!run && (!t?.needs_baseline || !!baseline), !!metric.trim(), true][step], [step, t, run, baseline, metric]);
  if (tpl.isLoading) return <div className="page"><Loading /></div>;
  return (
    <div className="page" style={{ maxWidth: 980 }}>
      <div className="page-head"><div><h1 className="row"><Wand2 size={20} color="var(--accent)" /> Guided study</h1>
        <div className="sub">Decide what would convince you <i>before</i> looking at the results — then check that the numbers can be trusted, then decide.</div></div></div>
      <div className="row" style={{ gap: 6, marginBottom: 14 }}>
        {STEPS.map((s, i) => <span key={s} className={`pill ${i === step ? "accent" : i < step ? "good" : "outline"}`}>{i < step ? <Check size={11} /> : null} {i + 1}. {s}</span>)}
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
        <button className="btn ghost" onClick={() => { if (!t || confirm("Cancel this guided study? Your answers are discarded.")) { reset(); nav("/studies"); } }}><X size={14} /> Cancel</button>
        <span className="spacer" />
        {step < 3 ? <button className="btn primary" disabled={!canNext} onClick={() => setStep(step + 1)}>Next <ArrowRight size={14} /></button>
          : <button className="btn primary" disabled={create.isPending} onClick={() => create.mutate()}>{create.isPending ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />} Create study</button>}
      </div>
    </div>
  );
}
