import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CloudOff, FileClock, Microscope, Plus, Search, Trash2, Wand2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { RunPicker } from "../components/RunPicker";
import { SubjectPicker } from "../components/SubjectPicker";
import { Card, Empty, ErrorBox, Loading, Pill, Tabs } from "../components/ui";
import { newDraftId, takeLocalDraft, useServerDraft, type DraftStatus } from "../lib/drafts";
import { fmtAgo } from "../lib/format";
import { useDebounced } from "../lib/hooks";

interface StudyDraft { title: string; goal: string; runs: string[]; baseline: string | null }
const EMPTY_DRAFT: StudyDraft = { title: "", goal: "", runs: [], baseline: null };
const isEmptyDraft = (d: StudyDraft): boolean => !(d.title || d.goal || d.runs.length || d.baseline);
const PAGE = 60;

export function DraftBadge({ status, savedAt }: { status: DraftStatus; savedAt: Date | null }) {
  if (status === "saving") return <span className="faint" style={{ fontSize: 12 }}>saving…</span>;
  if (status === "error") return <span className="warn-text row" style={{ fontSize: 12, gap: 4 }}><CloudOff size={12} /> not saved — check the connection</span>;
  if (status === "saved" && savedAt) return <span className="faint" style={{ fontSize: 12 }}>draft saved {fmtAgo(savedAt.toISOString())}</span>;
  return null;
}

/** All studies + your drafts + the "new study" form (prefilled from ?runs=a,b&baseline=c). A draft lives on the
 *  server (``?draft=<id>``): leave, restart, open another browser — Continue picks it up. */
export function StudiesPage() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const draftId = sp.get("draft");
  const route = (id: string): string => `/studies?draft=${encodeURIComponent(id)}`;
  const d = useServerDraft<StudyDraft>(draftId, "new-study", EMPTY_DRAFT, (v) => ({ title: v.title || (v.runs.length ? `Study of ${v.runs.length} run(s)` : "New study"), route: route(draftId ?? "") }), isEmptyDraft);
  const prefill = useRef<Partial<StudyDraft> | null>(null);
  const setDraftParam = (id: string | null): void => setSp((p) => { const n = new URLSearchParams(p); if (id) n.set("draft", id); else n.delete("draft"); n.delete("runs"); n.delete("baseline"); n.delete("new"); return n; }, { replace: true });

  // Coming from Runs / a run page: start a new draft with those runs.
  useEffect(() => {
    const fromUrl = (sp.get("runs") ?? "").split(",").filter(Boolean);
    if (!draftId && (fromUrl.length || sp.get("new") || sp.get("baseline"))) {
      prefill.current = { runs: fromUrl, baseline: sp.get("baseline") };
      setDraftParam(newDraftId("new-study"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);
  useEffect(() => {
    if (d.ready && draftId && prefill.current) {
      const p = prefill.current;
      prefill.current = null;
      d.set({ ...d.value, ...p, runs: [...new Set([...d.value.runs, ...(p.runs ?? [])])], baseline: p.baseline ?? d.value.baseline });
    }
  }, [d.ready, draftId, d]);
  // A draft kept in this browser by an older version moves to the server once.
  useEffect(() => {
    const old = takeLocalDraft(["mf.study.draft"], (read) => {
      const v = read("mf.study.draft") as (StudyDraft & { open?: boolean }) | undefined;
      return v && !isEmptyDraft({ ...EMPTY_DRAFT, ...v }) ? { ...EMPTY_DRAFT, ...v } : null;
    });
    if (old) {
      const id = newDraftId("new-study");
      void api.saveDraft(id, { kind: "new-study", title: old.title || "New study", route: route(id), payload: { title: old.title, goal: old.goal, runs: old.runs, baseline: old.baseline } })
        .then(() => qc.invalidateQueries({ queryKey: ["drafts"] }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { title, goal, runs, baseline } = d.value;
  const upd = (p: Partial<StudyDraft>): void => d.set({ ...d.value, ...p });
  const create = useMutation({
    mutationFn: () => api.createStudy({ title, goal, runs, baseline }),
    onSuccess: async (s) => { await d.discard(); void qc.invalidateQueries({ queryKey: ["studies-page"] }); nav(`/studies/${s.id}`); },
  });

  const [status, setStatus] = useState<"open" | "closed" | "all">("open");
  const [text, setText] = useState("");
  const query = useDebounced(text.trim(), 250);
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => setLimit(PAGE), [status, query]);
  const list = useQuery({
    queryKey: ["studies-page", status, query, limit],
    queryFn: () => api.studiesPage({ status: status === "all" ? undefined : status, q: query || undefined, limit }),
    placeholderData: (prev) => prev,
  });
  const drafts = useQuery({ queryKey: ["drafts"], queryFn: () => api.drafts() });
  const studyDrafts = (drafts.data?.drafts ?? []).filter((x) => x.kind === "new-study" || x.kind === "study-wizard");

  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <div className="page-head">
        <div>
          <h1>Studies</h1>
          <div className="sub">Study one experiment or a small group in depth: evidence → hypotheses → architecture change → verification.</div>
        </div>
        <div className="actions">
          <Link className="btn primary" to="/study-wizard"><Wand2 size={14} /> Guided study</Link>
          <button className="btn" onClick={() => setDraftParam(newDraftId("new-study"))}><Plus size={14} /> New study</button>
        </div>
      </div>
      {draftId && (
        <Card title="New study" sub={<DraftBadge status={d.status} savedAt={d.savedAt} />}
          right={<button className="btn ghost sm icon" title="Close (the draft is kept — continue it from Drafts)" onClick={() => setDraftParam(null)}><X size={14} /></button>}>
          {!d.ready ? <Loading /> : (
          <div className="col" style={{ gap: 12 }}>
            <label className="field">Title *<input className="input" autoFocus value={title} onChange={(e) => upd({ title: e.target.value })} placeholder="Why does the radar branch not help at long range?" /></label>
            <label className="field">Goal<textarea className="input" rows={2} value={goal} onChange={(e) => upd({ goal: e.target.value })} placeholder="Decide which change to the radar branch to make next." /></label>
            <div className="row wrap" style={{ alignItems: "flex-end" }}>
              <label className="field">Baseline<SubjectPicker value={baseline || null} onChange={(v) => upd({ baseline: v })} placeholder="Reference run…" label="Baseline" /></label>
              <label className="field">Candidates to study<RunPicker value={null} onChange={(v) => upd({ runs: [...new Set([...runs, v])] })} placeholder="Add a candidate run…" /></label>
            </div>
            {runs.length > 0 && (
              <div className="col" style={{ gap: 6 }} data-testid="study-candidates">
                <span className="faint" style={{ fontSize: 12 }}>Each candidate is one run, or several run ids that hold one model's results (<b>+ run</b>, e.g. its evaluation job).</span>
                <div className="row wrap" style={{ gap: 8, alignItems: "flex-start" }}>
                  {runs.map((r, i) => (
                    <div key={`${i}-${r}`} className="row" style={{ gap: 4, alignItems: "flex-start" }}>
                      <SubjectPicker value={r} width={300} label={`Candidate ${i + 1}`} onChange={(v) => upd({ runs: v ? runs.map((x, j) => (j === i ? v : x)) : runs.filter((_, j) => j !== i) })} />
                      <button className="btn ghost sm icon" aria-label={`Remove candidate ${i + 1}`} onClick={() => upd({ runs: runs.filter((_, j) => j !== i) })}><X size={13} /></button>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {create.error && <ErrorBox error={create.error} />}
            <div className="row" style={{ gap: 8 }}>
              <button className="btn ghost" onClick={() => { if (isEmptyDraft(d.value) || confirm("Discard this draft?")) void d.discard().then(() => setDraftParam(null)); }}><Trash2 size={14} /> Discard draft</button>
              <span className="spacer" />
              {!title.trim() && runs.length > 0 && <span className="faint">Give the study a title to create it</span>}
              <button className="btn primary" disabled={!title.trim() || create.isPending} onClick={() => create.mutate()}><Microscope size={14} /> Create study</button>
            </div>
          </div>)}
        </Card>
      )}
      {studyDrafts.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="sub-h row" style={{ gap: 6 }}><FileClock size={13} /> Drafts · {studyDrafts.length}</div>
          <div className="col" style={{ gap: 6 }}>
            {studyDrafts.map((x) => (
              <div key={x.id} className={`card row draft-row ${x.id === draftId ? "on" : ""}`} style={{ padding: "8px 12px", gap: 8 }}>
                <Pill tone="outline">{x.kind === "study-wizard" ? "guided" : "new study"}</Pill>
                <b>{x.title}</b><span className="faint" style={{ fontSize: 12 }}>edited {fmtAgo(x.updated_at)}</span><span className="spacer" />
                {x.id !== draftId && <Link className="btn sm" to={x.route}>Continue</Link>}
                <button className="btn sm ghost icon" title="Discard this draft" onClick={() => { if (confirm(`Discard “${x.title}”?`)) void api.deleteDraft(x.id).then(() => { if (x.id === draftId) setDraftParam(null); void qc.invalidateQueries({ queryKey: ["drafts"] }); }); }}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="row wrap" style={{ gap: 8, marginTop: 14 }}>
        <Tabs value={status} onChange={setStatus} tabs={[{ id: "open", label: "Open" }, { id: "closed", label: "Closed" }, { id: "all", label: "All" }]} />
        <span className="spacer" />
        <div className="input-icon" style={{ width: 320 }}><Search size={14} /><input className="input" aria-label="Search studies" placeholder="Search title, goal, owner…" value={text} onChange={(e) => setText(e.target.value)} /></div>
      </div>
      <div style={{ marginTop: 14 }}>
        {list.isLoading ? <Loading /> : list.error ? <ErrorBox error={list.error} /> : list.data!.items.length === 0 ? (
          query || status !== "open" ? <div className="muted">No {status === "all" ? "" : status} studies{query ? ` match “${query}”` : ""}.</div> :
          <Card><Empty icon={<BookOpen size={28} />} title="No studies yet">
            Start one from a run page (<b>Study</b> button), from the Runs table (select runs → <b>Start study</b>), or with <b>New study</b>.
          </Empty></Card>
        ) : (
          <div className="grid g2">
            {list.data!.items.map((s) => (
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
        {list.data && list.data.total > list.data.items.length && (
          <div className="row" style={{ justifyContent: "center", marginTop: 12 }}>
            <button className="btn" onClick={() => setLimit(limit + PAGE)} disabled={list.isFetching}>Load more ({list.data.items.length} of {list.data.total})</button>
          </div>
        )}
      </div>
    </div>
  );
}
