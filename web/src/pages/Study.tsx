import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, BookOpen, Gavel, Download, FileSearch, Lightbulb, Loader2, NotebookPen, Pencil, Star, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, refStr } from "../api/client";
import type { Study } from "../api/types";
import { useApp } from "../components/AppState";
import { InvestigationPanel } from "../components/Investigation";
import { RunPicker } from "../components/RunPicker";
import { Card, ErrorBox, Loading, Pill, StatusPill, Tabs } from "../components/ui";
import { fmtAgo, fmtDate } from "../lib/format";
import { DecideTab } from "./study/Decide";
import { EvidenceTab } from "./study/Evidence";
import { HypothesesTab } from "./study/Hypotheses";
import { HypothesisDialog, type HypothesisDraft } from "./study/HypothesisDialog";

type Tab = "decide" | "evidence" | "hypotheses" | "ai" | "journal";

export function StudyPage() {
  const id = useParams().studyId!;
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) ?? "evidence";
  const qc = useQueryClient();
  const nav = useNavigate();
  const { toast } = useApp();
  const study = useQuery({ queryKey: ["study", id], queryFn: () => api.study(id) });
  const runKey = study.data ? [...study.data.runs.map(refStr), study.data.baseline ? refStr(study.data.baseline) : ""].join("|") : "";
  const ev = useQuery({ queryKey: ["study-evidence", id, runKey], queryFn: () => api.studyEvidence(id), enabled: !!study.data });
  const [draft, setDraft] = useState<HypothesisDraft | null>(null);
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState("");

  const set = (s: Study): void => { qc.setQueryData(["study", id], s); void qc.invalidateQueries({ queryKey: ["studies"] }); };
  const m = <A,>(fn: (a: A) => Promise<Study>) => useMutation({ mutationFn: fn, onSuccess: set }); // eslint-disable-line react-hooks/rules-of-hooks
  const addRun = m((run: string) => api.studyAddRun(id, run));
  const rmRun = m((run: string) => api.studyRemoveRun(id, run));
  const setBase = m((run: string) => api.patchStudy(id, { baseline: run }));
  const patch = m((body: { title?: string; goal?: string }) => api.patchStudy(id, body));
  const pin = m(({ fid, pinned }: { fid: string; pinned: boolean }) => api.studyPin(id, fid, pinned));
  const addNote = m((text: string) => api.studyAddNote(id, text));
  const delNote = m((nid: string) => api.studyDeleteNote(id, nid));
  const attach = m((invId: string) => api.studyAttachInvestigation(id, invId));
  const addHyp = useMutation({
    mutationFn: (d: HypothesisDraft) => api.studyAddHypothesis(id, {
      ...d, evidence: (d.evidence ?? []).map((e) => ({ supports: true, run: null, ...e })) as never,
    }),
    onSuccess: (s) => { set(s); setDraft(null); toast("Hypothesis added", "good"); setSp({ tab: "hypotheses" }); },
  });

  if (study.isLoading) return <div className="page"><Loading label="Loading study…" /></div>;
  if (study.error || !study.data) return <div className="page"><ErrorBox error={study.error} /></div>;
  const s = study.data;
  const runs = s.runs.map(refStr);
  const base = s.baseline ? refStr(s.baseline) : null;
  const subject = runs.find((r) => r !== base) ?? base;
  const count = (k: string): number => s.hypotheses.filter((h) => h.status === k).length;
  const counts = { open: count("open"), supported: count("supported"), inconclusive: count("inconclusive"), rejected: count("rejected") };
  const pinnedFindings = (ev.data?.runs ?? []).flatMap((r) => r.findings).filter((f) => s.pinned_findings.includes(f.id));

  return (
    <div className="page">
      <div className="page-head">
        <div style={{ minWidth: 0, flex: 1 }}>
          {editing ? (
            <div className="col" style={{ gap: 8, maxWidth: 760 }}>
              <input className="input" style={{ fontSize: 18, height: 38 }} defaultValue={s.title} id="st-title" />
              <textarea className="input" rows={2} defaultValue={s.goal} id="st-goal" placeholder="Goal of this study" />
              <div className="row"><button className="btn primary sm" onClick={() => {
                patch.mutate({ title: (document.getElementById("st-title") as HTMLInputElement).value, goal: (document.getElementById("st-goal") as HTMLTextAreaElement).value });
                setEditing(false);
              }}>Save</button><button className="btn sm" onClick={() => setEditing(false)}>Cancel</button></div>
            </div>
          ) : (
            <>
              <div className="row"><h1 className="ellipsis">{s.title}</h1><button className="btn ghost sm icon" onClick={() => setEditing(true)}><Pencil size={13} /></button></div>
              <div className="sub">{s.goal || <span className="faint">No goal written yet.</span>}</div>
              <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
                <Pill tone="outline">{runs.length} runs</Pill>
                {counts.open > 0 && <Pill tone="info">{counts.open} open</Pill>}
                {counts.supported > 0 && <Pill tone="good">{counts.supported} supported</Pill>}
                {counts.inconclusive > 0 && <Pill tone="warn">{counts.inconclusive} inconclusive</Pill>}
                {counts.rejected > 0 && <Pill tone="bad">{counts.rejected} rejected</Pill>}
                <span className="faint" style={{ fontSize: 12 }}>updated {fmtAgo(s.updated_at)}</span>
              </div>
            </>
          )}
        </div>
        <div className="actions">
          <button className="btn" onClick={() => setDraft({ statement: "" })}><Lightbulb size={14} /> Hypothesis</button>
          <StudyBookButton id={id} />
          <a className="btn" href={api.studyExportUrl(id)} download={`study-${id}.md`}><Download size={14} /> Export</a>
          <button className="btn ghost icon" title="Delete study" onClick={() => {
            if (confirm(`Delete study “${s.title}”? Runs and findings are not affected.`)) void api.deleteStudy(id).then(() => { void qc.invalidateQueries({ queryKey: ["studies"] }); nav("/studies"); });
          }}><Trash2 size={14} /></button>
        </div>
      </div>

      <div className="split" style={{ gridTemplateColumns: "minmax(0,1fr) 320px" }}>
        <div style={{ minWidth: 0 }}>
          <Tabs<Tab> value={tab} onChange={(t) => setSp({ tab: t })} tabs={[
            { id: "decide", label: "Decide", icon: <Gavel size={14} />, count: s.decision ? 1 : 0 },
            { id: "evidence", label: "Evidence", icon: <FileSearch size={14} /> },
            { id: "hypotheses", label: "Hypotheses", icon: <Lightbulb size={14} />, count: s.hypotheses.length },
            { id: "ai", label: "AI", icon: <Bot size={14} />, count: s.investigations.length },
            { id: "journal", label: "Journal", icon: <NotebookPen size={14} />, count: s.notes.length },
          ]} />
          {tab === "evidence" && (ev.isLoading ? <Loading label="Collecting evidence…" /> : ev.error ? <ErrorBox error={ev.error} /> : ev.data &&
            <EvidenceTab ev={ev.data} pinned={s.pinned_findings} onPin={(fid, p) => pin.mutate({ fid, pinned: p })} onHypothesis={setDraft} />)}
          {tab === "decide" && <DecideTab study={s} onChange={set} />}
          {tab === "hypotheses" && <HypothesesTab study={s} onChange={set} onNew={() => setDraft({ statement: "" })} />}
          {tab === "ai" && (subject ? (
            <div className="col" style={{ gap: 14 }}>
              {s.investigations.length > 0 && (
                <Card title="Investigations in this study" flush>
                  {s.investigations.map((iid) => <Link key={iid} to={`/ai?inv=${iid}`} className="palette-item" style={{ borderRadius: 0 }}><Bot size={13} /> <span className="mono">{iid}</span></Link>)}
                </Card>
              )}
              <InvestigationPanel run={subject} baseline={base && base !== subject ? base : null} others={runs.filter((r) => r !== subject && r !== base)}
                onInvestigated={(inv) => attach.mutate(inv.id)}
                claimAction={(c, inv) => (c.type === "HYPOTHESIS" || c.type === "INFERENCE") && (
                  <button className="btn sm" onClick={() => setDraft({
                    statement: c.text, evidence: [{ kind: "claim", ref: `${inv.id}#${inv.claims.indexOf(c)}`, label: c.text.slice(0, 80) }],
                    source: `claim:${inv.id}#${inv.claims.indexOf(c)}`, reference_run: subject,
                  })}><Lightbulb size={12} /> Hypothesis</button>
                )} />
            </div>
          ) : <Card><span className="faint">Add a run to the study first.</span></Card>)}
          {tab === "journal" && (
            <div className="grid g2">
              <Card title="Notes">
                <div className="col" style={{ gap: 8 }}>
                  <textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you observe? What will you try next?" />
                  <div className="row"><span className="spacer" /><button className="btn primary sm" disabled={!note.trim()} onClick={() => { addNote.mutate(note); setNote(""); }}>Add note</button></div>
                  {[...s.notes].reverse().map((n) => (
                    <div key={n.id} className="ev" style={{ display: "block" }}>
                      <div className="row"><span className="faint" style={{ fontSize: 11.5 }}>{fmtDate(n.created_at)}</span><span className="spacer" />
                        <button className="btn ghost sm icon" onClick={() => delNote.mutate(n.id)}><X size={11} /></button></div>
                      <div style={{ whiteSpace: "pre-wrap" }}>{n.text}</div>
                    </div>
                  ))}
                </div>
              </Card>
              <Card title="Timeline" flush>
                <div style={{ maxHeight: 520, overflow: "auto" }}>
                  {[...s.timeline].reverse().map((e, i) => (
                    <div key={i} className="row" style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)", alignItems: "flex-start" }}>
                      <span className="tag" style={{ flex: "none" }}>{e.kind}</span><span style={{ flex: 1 }}>{e.text}</span>
                      <span className="faint" style={{ fontSize: 11.5, flex: "none" }}>{fmtAgo(e.at)}</span>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          )}
        </div>

        <div className="col" style={{ gap: 14 }}>
          <Card title="Runs" sub="★ = baseline" flush>
            {runs.map((r) => {
              const info = ev.data?.runs.find((x) => refStr(x.run.ref) === r)?.run;
              return (
                <div key={r} className="row" style={{ padding: "8px 12px", borderBottom: "1px solid var(--line)" }}>
                  <button className="btn ghost sm icon" title={r === base ? "Baseline" : "Make baseline"} onClick={() => r !== base && setBase.mutate(r)}>
                    <Star size={13} fill={r === base ? "var(--warn)" : "none"} color={r === base ? "var(--warn)" : "var(--text-3)"} />
                  </button>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <Link to={`/runs/${encodeURIComponent(r)}`} className="ellipsis" style={{ display: "block", fontWeight: 500 }}>{r.split(":").slice(1).join(":")}</Link>
                    <div className="faint ellipsis" style={{ fontSize: 11.5 }}>{info ? `${info.user ?? "—"} · ${info.description ?? ""}` : r.split(":")[0]}</div>
                  </div>
                  {info && <StatusPill status={info.status} />}
                  <button className="btn ghost sm icon" title="Remove from study" onClick={() => rmRun.mutate(r)}><X size={12} /></button>
                </div>
              );
            })}
            <div style={{ padding: 10 }}><RunPicker value={null} onChange={(v) => addRun.mutate(v)} placeholder="Add a run…" width={290} /></div>
          </Card>
          <Card title="Pinned findings" sub={`${s.pinned_findings.length}`} flush>
            {s.pinned_findings.length === 0 ? <div className="faint" style={{ padding: "10px 16px", fontSize: 12 }}>Pin findings from the Evidence tab to keep them in view.</div> :
              pinnedFindings.map((f) => (
                <div key={f.id} className="row" style={{ padding: "8px 12px", borderBottom: "1px solid var(--line)", alignItems: "flex-start" }}>
                  <div className={`sev-bar sev-${f.severity}`} style={{ alignSelf: "stretch" }} />
                  <div style={{ flex: 1, fontSize: 12.5 }}>{f.title}</div>
                  <button className="btn ghost sm icon" onClick={() => pin.mutate({ fid: f.id, pinned: false })}><X size={11} /></button>
                </div>
              ))}
          </Card>
          <Card title={<span className="row"><BookOpen size={14} /> How to use a study</span>}>
            <ol className="muted" style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7 }}>
              <li><b>Evidence</b>: compare runs, run the architecture diagnostics.</li>
              <li>Turn findings or AI claims into <b>hypotheses</b> with the change they suggest.</li>
              <li>Train the change, link it as the <b>test run</b>, press <b>Verify</b>.</li>
              <li>Mark supported / rejected; <b>Export</b> the journal.</li>
            </ol>
          </Card>
        </div>
      </div>
      {draft && <HypothesisDialog draft={draft} runs={runs} saving={addHyp.isPending} onCancel={() => setDraft(null)} onSave={(d) => addHyp.mutate(d)} />}
    </div>
  );
}

/** Generate the study book (background job) and open it when done. */
function StudyBookButton({ id }: { id: string }) {
  const { trackJob, toast, jobs } = useApp();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = jobs.find((j) => j.id === jobId);
  const running = !!job && job.status !== "done" && job.status !== "failed";
  useEffect(() => {
    if (job?.status === "done") { toast("Study book ready — click “open”", "good"); setJobId(null); }
    if (job?.status === "failed") { toast(`Study book failed: ${job.error ?? ""}`, "bad"); setJobId(null); }
  }, [job?.status, job?.error, id, toast]);
  return (
    <span className="row" style={{ gap: 4 }}>
      <button className="btn primary" disabled={running} title="Verdict, checks P1–P8, shared ruler, convergence, loss ledger, failures, next steps"
        onClick={() => void api.buildStudyBook(id, false).then((j) => { trackJob(j.job_id); setJobId(j.job_id); toast("Generating the study book — evaluation files are fetched once"); })}>
        {running ? <Loader2 size={14} className="spin" /> : <BookOpen size={14} />} Study book
      </button>
      <a className="btn ghost sm" href={api.studyBookUrl(id)} target="_blank" rel="noreferrer" title="Open the last generated book">open</a>
    </span>
  );
}
