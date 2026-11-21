import { useQuery } from "@tanstack/react-query";
import { FlaskConical, MessageSquareText, Search } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { PromptTemplate } from "../api/types";
import { evidenceSummary, isMultiRun } from "../lib/prompts";
import { api } from "../api/client";
import { Card, Loading, Pill, Tabs } from "../components/ui";

export function LibraryPage() {
  const [tab, setTab] = useState<"analyses" | "prompts">("analyses");
  const analyses = useQuery({ queryKey: ["analyses"], queryFn: api.analyses });
  const prompts = useQuery({ queryKey: ["prompts"], queryFn: api.prompts });
  return (
    <div className="page">
      <div className="page-head"><div><h1>Library</h1><div className="sub">Deterministic analysis packs and the versioned prompt library.</div></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "analyses", label: "Analyses", icon: <FlaskConical size={14} />, count: analyses.data?.length },
        { id: "prompts", label: "Prompts", icon: <MessageSquareText size={14} />, count: prompts.data?.length },
      ]} />
      {tab === "analyses" && (analyses.isLoading ? <Loading /> : (
        <div className="grid g2">
          {analyses.data!.map((a) => (
            <Card key={a.name} title={a.title} right={<><Pill tone="outline">{a.category}</Pill><span className="mono faint">v{a.version}</span></>}>
              <div className="col" style={{ gap: 10 }}>
                <div className="muted">{a.description}</div>
                <div className="row wrap" style={{ gap: 6 }}>{a.requires.map((r) => <Pill key={r} tone="info">requires {r}</Pill>)}</div>
                {a.params.properties && (
                  <div>
                    <div className="sub-h">Parameters</div>
                    <table className="t compact"><tbody>
                      {Object.entries(a.params.properties).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="mono faint">{JSON.stringify(v.default)}</td></tr>)}
                    </tbody></table>
                  </div>
                )}
                {a.limitations.length > 0 && <div className="faint" style={{ fontSize: 12 }}>Limitations: {a.limitations.join(" ")}</div>}
              </div>
            </Card>
          ))}
        </div>
      ))}
      {tab === "prompts" && (prompts.isLoading ? <Loading /> : <PromptList prompts={prompts.data ?? []} />)}
    </div>
  );
}

function PromptList({ prompts }: { prompts: PromptTemplate[] }) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("all");
  const nav = useNavigate();
  const cats = [...new Set(prompts.map((p) => p.category))].sort();
  const t = q.toLowerCase();
  const shown = prompts.filter((p) => (cat === "all" || p.category === cat) && (!t || [p.title, p.description, p.id, ...p.tags].join(" ").toLowerCase().includes(t)));
  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="row wrap">
        <div className="input-wrap" style={{ width: 340 }}><Search size={14} /><input className="input" placeholder="Search templates" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <button className={`btn sm ${cat === "all" ? "primary" : ""}`} onClick={() => setCat("all")}>All · {prompts.length}</button>
        {cats.map((c) => <button key={c} className={`btn sm ${cat === c ? "primary" : ""}`} onClick={() => setCat(c)}>{c} · {prompts.filter((p) => p.category === c).length}</button>)}
      </div>
      <div className="faint" style={{ fontSize: 12 }}>
        Templates live in <code>backend/src/model_forensic/ai/prompt_packs/*.yaml</code>; add your own packs via <code>prompt_dirs</code> in <code>model-forensic.yaml</code>.
      </div>
      <div className="grid g2">
        {shown.map((p) => (
          <Card key={p.id} title={p.title} right={<><span className="tag">{p.category}</span><span className="mono faint">{p.id}@{p.version}</span></>}>
            <div className="col" style={{ gap: 10 }}>
              <div className="muted">{p.description}</div>
              <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: 0, color: "var(--text-2)", maxHeight: 180, overflow: "auto", fontSize: 11.5 }}>{p.template}</pre>
              <div className="row wrap" style={{ gap: 6 }}>
                {p.needs_baseline && <Pill tone="info">needs baseline</Pill>}
                {isMultiRun(p) && <Pill tone="good">multi-run</Pill>}
                {p.variables.map((v) => <Pill key={v} tone="outline">{`{{${v}}}`}{p.requires.includes(v) ? " *" : ""}</Pill>)}
                {evidenceSummary(p).map((e) => <span key={e} className="tag">{e}</span>)}
                <span className="spacer" />
                <button className="btn sm" onClick={() => nav(`/ai`)}>Use in AI →</button>
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
