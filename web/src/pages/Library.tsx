import { useQuery } from "@tanstack/react-query";
import { FlaskConical, MessageSquareText } from "lucide-react";
import { useState } from "react";
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
      {tab === "prompts" && (prompts.isLoading ? <Loading /> : (
        <div className="grid g2">
          {prompts.data!.map((p) => (
            <Card key={p.id} title={p.title} right={<span className="mono faint">{p.id}@{p.version}</span>}>
              <pre className="mono" style={{ whiteSpace: "pre-wrap", margin: 0, color: "var(--text-2)" }}>{p.template}</pre>
              <div className="row wrap" style={{ gap: 6, marginTop: 10 }}>
                {p.tags.map((t) => <span key={t} className="tag">#{t}</span>)}
                {p.requires.map((r) => <Pill key={r} tone="outline">{`{{${r}}}`}</Pill>)}
              </div>
            </Card>
          ))}
        </div>
      ))}
    </div>
  );
}
