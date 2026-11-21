import { useMutation, useQuery } from "@tanstack/react-query";
import { BookOpen, Microscope, Plus, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { RunPicker } from "../components/RunPicker";
import { Card, Empty, ErrorBox, Loading, Pill } from "../components/ui";
import { fmtAgo } from "../lib/format";

/** All studies + "new study" form (prefilled from ?runs=a,b&baseline=c when coming from Runs / a run page). */
export function StudiesPage() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const list = useQuery({ queryKey: ["studies"], queryFn: api.studies });
  const [open, setOpen] = useState(!!sp.get("runs") || !!sp.get("new"));
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [runs, setRuns] = useState<string[]>((sp.get("runs") ?? "").split(",").filter(Boolean));
  const [baseline, setBaseline] = useState<string | null>(sp.get("baseline"));
  const create = useMutation({
    mutationFn: () => api.createStudy({ title, goal, runs, baseline }),
    onSuccess: (s) => nav(`/studies/${s.id}`),
  });

  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <div className="page-head">
        <div>
          <h1>Studies</h1>
          <div className="sub">Study one experiment or a small group in depth: evidence → hypotheses → architecture change → verification.</div>
        </div>
        <div className="actions"><button className="btn primary" onClick={() => setOpen(true)}><Plus size={14} /> New study</button></div>
      </div>
      {open && (
        <Card title="New study" right={<button className="btn ghost sm icon" onClick={() => setOpen(false)}><X size={14} /></button>}>
          <div className="col" style={{ gap: 12 }}>
            <label className="field">Title *<input className="input" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Why does the radar branch not help at long range?" /></label>
            <label className="field">Goal<textarea className="input" rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Decide which change to the radar branch to make next." /></label>
            <div className="row wrap" style={{ alignItems: "flex-end" }}>
              <label className="field">Baseline<RunPicker value={baseline} onChange={setBaseline} placeholder="Reference run…" /></label>
              <label className="field">Runs to study<RunPicker value={null} onChange={(v) => setRuns([...new Set([...runs, v])])} placeholder="Add run…" /></label>
            </div>
            {runs.length > 0 && (
              <div className="row wrap" style={{ gap: 6 }}>
                {runs.map((r) => <span key={r} className="tag row" style={{ gap: 4 }}>{r}<X size={11} style={{ cursor: "pointer" }} onClick={() => setRuns(runs.filter((x) => x !== r))} /></span>)}
              </div>
            )}
            {create.error && <ErrorBox error={create.error} />}
            <div className="row"><span className="spacer" /><button className="btn primary" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}><Microscope size={14} /> Create study</button></div>
          </div>
        </Card>
      )}
      <div style={{ marginTop: 14 }}>
        {list.isLoading ? <Loading /> : (list.data ?? []).length === 0 ? (
          <Card><Empty icon={<BookOpen size={28} />} title="No studies yet">
            Start one from a run page (<b>Study</b> button), from the Runs table (select runs → <b>Start study</b>), or with <b>New study</b>.
          </Empty></Card>
        ) : (
          <div className="grid g2">
            {list.data!.map((s) => (
              <Link key={s.id} to={`/studies/${s.id}`} className="card" style={{ padding: 16, display: "block" }}>
                <div className="row"><Microscope size={15} color="var(--accent-2)" /><b style={{ fontSize: 14 }}>{s.title}</b><span className="spacer" /><span className="faint" style={{ fontSize: 12 }}>{fmtAgo(s.updated_at)}</span></div>
                {s.goal && <div className="muted" style={{ marginTop: 6 }}>{s.goal}</div>}
                <div className="row wrap" style={{ gap: 6, marginTop: 10 }}>
                  <Pill tone="outline">{s.runs} runs</Pill>
                  {s.hypotheses.open > 0 && <Pill tone="info">{s.hypotheses.open} open</Pill>}
                  {s.hypotheses.supported > 0 && <Pill tone="good">{s.hypotheses.supported} supported</Pill>}
                  {s.hypotheses.rejected > 0 && <Pill tone="bad">{s.hypotheses.rejected} rejected</Pill>}
                  {s.hypotheses.inconclusive > 0 && <Pill tone="warn">{s.hypotheses.inconclusive} inconclusive</Pill>}
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
