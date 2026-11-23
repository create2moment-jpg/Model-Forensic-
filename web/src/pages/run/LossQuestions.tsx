import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChartLine, CheckCircle2, ChevronDown, ChevronRight, MinusCircle, XCircle } from "lucide-react";
import { useState } from "react";
import { api, type LossQuestionDto } from "../../api/client";
import { Card, Loading, Seg } from "../../components/ui";
import { useLocalStorage } from "../../lib/hooks";
import { StageSelect } from "../../components/StageSelect";

const ICON = {
  bad: <XCircle size={15} style={{ color: "var(--bad)" }} />,
  warn: <AlertTriangle size={15} style={{ color: "var(--warn, #f5b84b)" }} />,
  ok: <CheckCircle2 size={15} style={{ color: "var(--good)" }} />,
  na: <MinusCircle size={15} style={{ color: "var(--text-3)" }} />,
};
const RANK = { bad: 0, warn: 1, ok: 2, na: 3 };

/** The questions to ask of a run's losses — is the loss diverging? the layer refinement? is a task muted? … —
 *  each answered from the series (deterministic), with the series to plot, where to look and what to try next.
 *  With a baseline, the comparison questions too. */
export function LossQuestions({ runId, baseline, onPlot, stages = [], stage = "", onStage }: { runId: string; baseline: string | null; onPlot: (keys: string[]) => void; stages?: string[]; stage?: string; onStage?: (s: string) => void }) {
  const q = useQuery({ queryKey: ["loss-questions", runId, baseline, stage], queryFn: () => api.lossQuestions(runId, baseline, stage || null), staleTime: 60_000 });
  const [show, setShow] = useLocalStorage<"problems" | "all">("mf.questions.show", "problems");
  const [open, setOpen] = useState<string | null>(null);
  const qs = [...(q.data?.questions ?? [])].sort((a, b) => RANK[a.status] - RANK[b.status]);
  const n = (s: LossQuestionDto["status"]): number => qs.filter((x) => x.status === s).length;
  const shown = show === "all" ? qs : qs.filter((x) => x.status === "bad" || x.status === "warn");
  const sub = q.data ? `${n("bad")} problem${n("bad") === 1 ? "" : "s"} · ${n("warn")} to look at · ${n("ok")} fine · ${n("na")} not logged` : "answered from this run's series";
  return (
    <Card fold="metrics.questions" title="Training health questions" sub={sub} flush
      right={<>{onStage && <StageSelect stages={stages} value={stage} onChange={onStage} />}<Seg options={[{ id: "problems", label: "Problems" }, { id: "all", label: `All ${qs.length || ""}` }]} value={show} onChange={setShow} /></>}>
      {q.isLoading ? <Loading label="Answering…" /> : q.error ? <div className="faint" style={{ padding: 12 }}>Could not answer: {String(q.error)}</div> : (
        <div className="questions" data-testid="loss-questions">
          {shown.length === 0 && <div className="faint" style={{ padding: "10px 14px" }}>No problems found — <button className="btn ghost sm" onClick={() => setShow("all")}>show all {qs.length} questions</button></div>}
          {shown.map((x) => (
            <div key={x.id} className={`question ${x.status}`} data-qid={x.id} data-open={open === x.id ? "" : undefined}>
              <div className="q-row">
                {ICON[x.status]}
                <button type="button" className="q-text" aria-expanded={open === x.id} onClick={() => setOpen(open === x.id ? null : x.id)}>
                  <span className="q-chev">{open === x.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
                  <span className="q-body">
                    <b>{x.question}</b>
                    <span className="q-answer" title={x.answer}>{x.answer}</span>
                  </span>
                </button>
                <span className="faint q-area">{x.area}</span>
                {x.keys.length > 0 && <button type="button" className="btn ghost sm" title={`Plot: ${x.keys.join(", ")}`} onClick={() => onPlot(x.keys)}><ChartLine size={13} /> Plot</button>}
              </div>
              {open === x.id && (
                <div className="q-detail">
                  {x.look && <div><span className="sub-h">Where to look</span>{x.look}</div>}
                  {x.ideas.length > 0 && <div><span className="sub-h">{x.status === "bad" || x.status === "warn" ? "What to try" : "If it happens"}</span><ul>{x.ideas.map((i) => <li key={i}>{i}</li>)}</ul></div>}
                  {x.why && <div className="faint"><span className="sub-h">Why it matters</span>{x.why}</div>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
