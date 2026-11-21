import { useSearchParams } from "react-router-dom";
import { InvestigationPanel } from "../components/Investigation";

export function AiPage() {
  const [sp, setSp] = useSearchParams();
  const set = (k: string, v: string | null): void => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n); };
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>AI investigation</h1>
          <div className="sub">Your model interprets deterministic evidence. Every claim is labelled and checked against cited evidence; unverifiable claims are flagged.</div>
        </div>
      </div>
      <InvestigationPanel run={sp.get("run")} baseline={sp.get("baseline")} initialId={sp.get("inv")} onRunChange={(v) => set("run", v)}
        onBaselineChange={(v) => set("baseline", v)} others={(sp.get("others") ?? "").split(",").filter(Boolean)}
        onOthersChange={(v) => set("others", v.join(",") || null)} />
    </div>
  );
}
