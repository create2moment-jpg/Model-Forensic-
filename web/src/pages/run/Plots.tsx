import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import type { PlotSpecV1, PlotValidation } from "../../api/types";
import { PlotView } from "../../components/PlotView";
import { Card, ErrorBox, Loading } from "../../components/ui";

/** Plots as validated specs: rule-based suggestions, the local-LLM plotter (every proposal is validated — it can
 *  only plot keys the run has), and a spec editor with schema + grounding checks. */
export function PlotsTab({ runId }: { runId: string }) {
  const sug = useQuery({ queryKey: ["plot-suggest", runId], queryFn: () => api.plotSuggest(runId) });
  const [question, setQuestion] = useState("");
  const ai = useMutation({ mutationFn: () => api.plotAi(runId, question) });
  const [text, setText] = useState("");
  const [check, setCheck] = useState<PlotValidation | null>(null);
  const [custom, setCustom] = useState<PlotSpecV1 | null>(null);
  const validate = async (): Promise<void> => {
    let doc: unknown;
    try { doc = JSON.parse(text); } catch (e) { setCheck({ ok: false, errors: [`not JSON: ${String(e)}`], warnings: [], spec: null }); return; }
    const v = await api.plotValidate(doc);
    setCheck(v);
    setCustom(v.ok ? v.spec : null);
  };
  if (sug.isLoading) return <Loading />;
  if (sug.error || !sug.data) return <ErrorBox error={sug.error} />;
  const aiSpecs = ai.data ? (ai.data.specs.length ? ai.data.specs : ai.data.fallback ?? []) : [];
  return (
    <div className="col" style={{ gap: 14 }}>
      <Card title={<span className="row"><Bot size={15} /> Ask the plotter</span>} sub="The local LLM proposes plot specs; each one is validated against the run's metric keys before it is drawn">
        <form className="row" onSubmit={(e) => { e.preventDefault(); ai.mutate(); }}>
          <input className="input" style={{ flex: 1 }} aria-label="Plot question" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. is the validation loss diverging from training?" />
          <button className="btn primary" disabled={ai.isPending}>Suggest plots</button>
        </form>
        {ai.data?.note && <div className="note" style={{ marginTop: 8 }}>{ai.data.note}</div>}
        {ai.data?.rejected.map((r, i) => <div key={i} className="note warn" style={{ marginTop: 6 }}><XCircle size={14} /> rejected: {r.errors.join("; ")}</div>)}
      </Card>
      {aiSpecs.map((s) => <PlotView key={`ai-${s.id}`} spec={s} />)}
      <div className="grid2">{sug.data.specs.map((s) => <PlotView key={s.id} spec={s} />)}</div>
      {sug.data.specs.length === 0 && <div className="muted">No metric series to plot yet.</div>}
      <Card title="Plot spec editor" sub="Paste a PlotSpec v1 (JSON) — the schema is at /api/v1/plots/schema">
        <textarea className="input mono" rows={8} aria-label="Plot spec" value={text} onChange={(e) => setText(e.target.value)}
          placeholder={JSON.stringify(sug.data.specs[0] ?? { id: "my-plot", title: "My plot", series: [{ run: runId, key: "val/loss" }] }, null, 1)} />
        <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => void validate()}>Validate &amp; draw</button></div>
        {check && (check.ok
          ? <div className="note" data-testid="plot-ok"><CheckCircle2 size={14} /> valid{check.warnings.length ? ` — ${check.warnings.join("; ")}` : ""}</div>
          : <div className="note warn" data-testid="plot-errors"><XCircle size={14} /> <div>{check.errors.map((e) => <div key={e} className="mono" style={{ fontSize: 12 }}>{e}</div>)}</div></div>)}
      </Card>
      {custom && <PlotView spec={custom} />}
    </div>
  );
}
