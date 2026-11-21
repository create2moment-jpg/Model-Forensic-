import { ArrowRight, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useLocalStorage } from "../lib/hooks";

const STEPS: { title: string; text: string; to: string; cta: string }[] = [
  { title: "Welcome to Model Forensic", text: "From “something is wrong with my training” to evidence, a verdict you can trust and the next experiment — read-only on your Azure ML runs.", to: "/", cta: "Start" },
  { title: "Check my run", text: "Pick a run and get at most five plain-language cards: what it means, why it matters, what to do next.", to: "/check", cta: "Show me" },
  { title: "Findings with evidence", text: "Every run page runs deterministic analyses — pitfall checks, loss ledger, failures by distance and scene — each finding with its evidence and the next experiment as a config diff.", to: "/runs", cta: "Open runs" },
  { title: "Guided study", text: "Pre-register what would convince you, run the checks first, then record the decision. The checks block a verdict when the numbers can't be trusted.", to: "/study-wizard", cta: "Start a study" },
  { title: "Study book", text: "Run ids in, a forensic book out: verdict, checks, shared ruler, convergence, loss ledger, failures, next steps — in seconds.", to: "/studies", cta: "See studies" },
];

/** A five-step tour on the first visit; skippable, can be restarted from the command palette (`?tour=1`). */
export function Tour() {
  const [done, setDone] = useLocalStorage("mf.tour.done", false);
  const [i, setI] = useState(0);
  const nav = useNavigate();
  const forced = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("tour") === "1";
  if (done && !forced) return null;
  const s = STEPS[i]!;
  const close = (): void => { setDone(true); if (forced) nav(window.location.pathname); };
  return (
    <div className="tour" role="dialog" aria-label="Guided tour">
      <div className="row"><b>{s.title}</b><span className="spacer" /><span className="faint" style={{ fontSize: 12 }}>{i + 1}/{STEPS.length}</span>
        <button className="btn ghost icon sm" aria-label="Close tour" onClick={close}><X size={14} /></button></div>
      <p className="muted" style={{ margin: "8px 0 12px" }}>{s.text}</p>
      <div className="row" style={{ gap: 6 }}>
        <button className="btn sm ghost" onClick={close}>Skip tour</button>
        <span className="spacer" />
        <button className="btn sm" onClick={() => nav(s.to)}>{s.cta}</button>
        {i + 1 < STEPS.length
          ? <button className="btn sm primary" onClick={() => { setI(i + 1); nav(STEPS[i + 1]!.to); }}>Next <ArrowRight size={13} /></button>
          : <button className="btn sm primary" onClick={close}>Done</button>}
      </div>
    </div>
  );
}
