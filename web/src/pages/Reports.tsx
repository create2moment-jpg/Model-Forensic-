import { useMutation } from "@tanstack/react-query";
import { Download, FileText, Loader2, Printer } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { RunPicker } from "../components/RunPicker";
import { Card, Empty, ErrorBox } from "../components/ui";

export function ReportsPage() {
  const [sp, setSp] = useSearchParams();
  const run = sp.get("run");
  const baseline = sp.get("baseline");
  const set = (k: string, v: string): void => { const n = new URLSearchParams(sp); n.set(k, v); setSp(n); };
  const html = useMutation({ mutationFn: () => api.report({ run: run!, baseline, format: "html" }) });
  const save = async (): Promise<void> => {
    const md = await api.report({ run: run!, baseline, format: "markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    a.download = `forensic-report-${run!.split(":").pop()}.md`;
    a.click();
  };
  const print = (): void => {
    const w = window.open("", "_blank");
    if (!w || !html.data) return;
    w.document.write(html.data);
    w.document.close();
    setTimeout(() => w.print(), 300);
  };
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Reports</h1><div className="sub">Reproducible forensic reports generated from stored findings, comparisons and provenance.</div></div>
      </div>
      <div className="card row wrap" style={{ padding: 12, marginBottom: 14, gap: 12, alignItems: "flex-end" }}>
        <label className="field">Run<RunPicker value={run} onChange={(v) => set("run", v)} width={300} /></label>
        <label className="field">Baseline (optional)<RunPicker value={baseline} onChange={(v) => set("baseline", v)} placeholder="No baseline" width={300} /></label>
        <button className="btn primary" disabled={!run || html.isPending} onClick={() => html.mutate()}>{html.isPending ? <Loader2 size={14} className="spin" /> : <FileText size={14} />} Generate</button>
        <span className="spacer" />
        <button className="btn" disabled={!run} onClick={() => void save()}><Download size={14} /> Markdown</button>
        <button className="btn" disabled={!html.data} onClick={print}><Printer size={14} /> PDF (print)</button>
      </div>
      {html.error && <ErrorBox error={html.error} />}
      {html.data ? (
        <Card flush><iframe title="report" srcDoc={html.data} style={{ width: "100%", height: "calc(100vh - 260px)", border: 0, borderRadius: 12, background: "white" }} sandbox="allow-same-origin allow-modals" /></Card>
      ) : <Card><Empty icon={<FileText size={28} />} title="No report yet">Select a run (and optionally a baseline) and press Generate. Reports include executive summary, config diff, metric comparison, findings, hypotheses, recommended experiments, limitations and provenance.</Empty></Card>}
    </div>
  );
}
