import { useMutation, useQuery } from "@tanstack/react-query";
import { BookOpen, Microscope, Plus, Trash2, Wand2, X } from "lucide-react";
import { useEffect } from "react";
import { useLocalStorage } from "../lib/hooks";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { RunPicker } from "../components/RunPicker";
import { Card, Empty, ErrorBox, Loading, Pill } from "../components/ui";
import { fmtAgo } from "../lib/format";

interface StudyDraft { open: boolean; title: string; goal: string; runs: string[]; baseline: string | null }
const EMPTY_DRAFT: StudyDraft = { open: false, title: "", goal: "", runs: [], baseline: null };

/** All studies + "new study" form (prefilled from ?runs=a,b&baseline=c when coming from Runs / a run page). */
export function StudiesPage() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const list = useQuery({ queryKey: ["studies"], queryFn: api.studies });
  // The draft survives leaving the page (e.g. to Settings) — kept in this browser until created or discarded.
  const fromUrl = (sp.get("runs") ?? "").split(",").filter(Boolean);
  const [draft, setDraft] = useLocalStorage<StudyDraft>("mf.study.draft", EMPTY_DRAFT);
  useEffect(() => {
    if (fromUrl.length || sp.get("new") || sp.get("baseline")) {
      setDraft({ ...draft, open: true, runs: [...new Set([...draft.runs, ...fromUrl])], baseline: sp.get("baseline") ?? draft.baseline });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);
  const { open, title, goal, runs, baseline } = draft;
  const upd = (p: Partial<StudyDraft>): void => setDraft({ ...draft, ...p });
  const setOpen = (v: boolean): void => upd({ open: v });
  const setTitle = (v: string): void => upd({ title: v });
  const setGoal = (v: string): void => upd({ goal: v });
  const setRuns = (v: string[]): void => upd({ runs: v });
  const setBaseline = (v: string | null): void => upd({ baseline: v });
  const discard = (): void => setDraft(EMPTY_DRAFT);
  const hasDraft = !!(title || goal || runs.length || baseline);
  const create = useMutation({
    mutationFn: () => api.createStudy({ title, goal, runs, baseline }),
    onSuccess: (s) => { setDraft(EMPTY_DRAFT); nav(`/studies/${s.id}`); },
  });

  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <div className="page-head">
        <div>
          <h1>Studies</h1>
          <div className="sub">Study one experiment or a small group in depth: evidence → hypotheses → architecture change → verification.</div>
        </div>
        <div className="actions">
          <Link className="btn primary" to="/study-wizard"><Wand2 size={14} /> Guided study</Link>
          <button className="btn" onClick={() => setOpen(true)}><Plus size={14} /> {hasDraft && !open ? "Continue draft" : "New study"}</button>
        </div>
      </div>
      {open && (
        <Card title="New study" sub={hasDraft ? "draft — kept if you leave this page" : undefined}
          right={<button className="btn ghost sm icon" title="Hide (the draft is kept)" onClick={() => setOpen(false)}><X size={14} /></button>}>
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
            <div className="row" style={{ gap: 8 }}>
              <button className="btn ghost" onClick={() => { if (!hasDraft || confirm("Discard this draft?")) discard(); }}><Trash2 size={14} /> Cancel and discard draft</button>
              <span className="spacer" />
              {!title.trim() && runs.length > 0 && <span className="faint">Give the study a title to create it</span>}
              <button className="btn primary" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}><Microscope size={14} /> Create study</button>
            </div>
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
                <div className="row"><Microscope size={15} color="var(--accent-2)" /><b style={{ fontSize: 14 }}>{s.title}</b>{s.status === "closed" && <span className="tag">closed</span>}<span className="spacer" /><span className="faint" style={{ fontSize: 12 }}>{fmtAgo(s.updated_at)}</span></div>
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
