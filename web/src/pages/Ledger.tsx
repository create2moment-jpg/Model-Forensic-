import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookMarked, Download, GitCompare, NotebookPen, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type LedgerEntry, type LedgerOutcome } from "../api/client";
import { useApp } from "../components/AppState";
import { SubjectPicker } from "../components/SubjectPicker";
import { Card, Empty, ErrorBox, Loading, Pill, Seg } from "../components/ui";
import { fmtAgo, fmtNum } from "../lib/format";

const STATUS_TONE: Record<string, string> = { planned: "outline", running: "info", analysed: "accent", decided: "good" };
const DECISION_TONE: Record<string, string> = { adopt: "good", reject: "bad", inconclusive: "warn", "follow-up": "info" };
const DECISIONS = ["adopt", "reject", "inconclusive", "follow-up"] as const;
const short = (r: string): string => r.split(":").pop()!;

/** The experiment ledger: hypothesis → change → outcome → decision, one entry per experiment. The outcome is
 *  computed (config changes, moved metrics, training health problems); hypothesis and decision are written by you. */
export function LedgerPage() {
  const [sp, setSp] = useSearchParams();
  const sel = sp.get("id");
  const [status, setStatus] = useState<"all" | "planned" | "analysed" | "decided">("all");
  const [text, setText] = useState("");
  const qc = useQueryClient();
  const { toast, pending } = useApp();
  const list = useQuery({ queryKey: ["ledger", status, text], queryFn: () => api.ledger(status === "all" ? undefined : status, text || undefined) });
  const create = useMutation({
    mutationFn: () => api.ledgerCreate({ title: "New experiment", analyse: false }),
    onSuccess: (e) => { void qc.invalidateQueries({ queryKey: ["ledger"] }); setSp({ id: e.id }); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const open = (id: string): void => setSp({ id });
  return (
    <div className="page">
      <div className="page-head">
        <div><h1><NotebookPen size={20} style={{ verticalAlign: -3 }} /> Experiment ledger</h1><div className="sub">What was tried, what it did, what was decided — hypothesis and decision by you, the outcome computed from the runs.</div></div>
        <div className="actions">
          <a className="btn" href="/api/v1/ledger.md" download="ledger.md"><Download size={14} /> Export Markdown</a>
          <button className="btn primary" onClick={() => create.mutate()}><Plus size={14} /> New entry</button>
        </div>
      </div>
      <div className="ledger-layout">
        <div className="col" style={{ gap: 10, minWidth: 0 }}>
          <div className="card col" style={{ padding: 10, gap: 8 }}>
            <div className="input-wrap"><Search size={13} /><input className="input" aria-label="Search the ledger" placeholder="Search title, runs, tags" value={text} onChange={(e) => setText(e.target.value)} /></div>
            <Seg options={[{ id: "all", label: "All" }, { id: "planned", label: "Planned" }, { id: "analysed", label: "Analysed" }, { id: "decided", label: "Decided" }]} value={status} onChange={setStatus} />
          </div>
          {list.isLoading ? <Loading /> : list.error ? <ErrorBox error={list.error} /> : list.data!.entries.length === 0 ? (
            <Card><Empty icon={<NotebookPen size={26} />} title="No entries yet">Start one here, or press <b>Add to ledger</b> on a comparison.</Empty></Card>
          ) : (
            <div className="col" style={{ gap: 6 }} data-testid="ledger-list">
              {list.data!.entries.filter((e) => !pending(`ledger:${e.id}`)).map((e) => (
                <button key={e.id} type="button" className={`ledger-item ${sel === e.id ? "on" : ""}`} onClick={() => open(e.id)}>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="mono faint" style={{ fontSize: 11 }}>{e.id}</span>
                    <span className="spacer" />
                    {e.decision && <Pill tone={DECISION_TONE[e.decision]}>{e.decision}</Pill>}
                    <Pill tone={STATUS_TONE[e.status]}>{e.status}</Pill>
                  </div>
                  <b className="trunc" title={e.title}>{e.title}</b>
                  {e.headline && <span className="faint trunc mono" style={{ fontSize: 11 }} title={e.headline}>{e.headline}</span>}
                  <span className="faint" style={{ fontSize: 11 }}>{fmtAgo(e.updated_at)}{e.tags.length ? ` · ${e.tags.join(", ")}` : ""}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div style={{ minWidth: 0 }}>
          {sel ? <EntryEditor key={sel} id={sel} onDeleted={() => setSp({})} /> : <Card><Empty title="Pick an entry" >or start a new one — the outcome fills in once it has a baseline and a candidate.</Empty></Card>}
        </div>
      </div>
    </div>
  );
}

function EntryEditor({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { toast } = useApp();
  const q = useQuery({ queryKey: ["ledger-entry", id], queryFn: () => api.ledgerEntry(id) });
  const [form, setForm] = useState<LedgerEntry | null>(null);
  useEffect(() => { if (q.data) setForm(q.data); }, [q.data]);
  const done = (e: LedgerEntry): void => { qc.setQueryData(["ledger-entry", id], e); void qc.invalidateQueries({ queryKey: ["ledger"] }); };
  const save = useMutation({
    mutationFn: (f: LedgerEntry) => api.ledgerUpdate(id, { title: f.title, hypothesis: f.hypothesis, change: f.change, baseline: f.baseline, candidates: f.candidates, decision: f.decision, decision_note: f.decision_note, notes: f.notes, tags: f.tags }),
    onSuccess: (e) => { done(e); toast("Saved", "good"); }, onError: (e: Error) => toast(e.message, "bad"),
  });
  const refresh = useMutation({ mutationFn: () => api.ledgerRefresh(id), onSuccess: done, onError: (e: Error) => toast(e.message, "bad") });
  const promote = useMutation({
    mutationFn: () => api.ledgerPromote(id),
    onSuccess: (r) => toast(r.added.length ? `Proposed ${r.added.join(", ")} — accept it in Knowledge` : r.errors.join("; "), r.added.length ? "good" : "bad"),
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const { later } = useApp();
  const del = useMutation({ mutationFn: () => api.ledgerDelete(id), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["ledger"] }); } });
  if (q.isLoading || !form) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const set = <K extends keyof LedgerEntry>(k: K, v: LedgerEntry[K]): void => setForm({ ...form, [k]: v });
  const dirty = JSON.stringify(form) !== JSON.stringify(q.data);
  const runsChanged = form.baseline !== q.data!.baseline || JSON.stringify(form.candidates) !== JSON.stringify(q.data!.candidates);
  return (
    <div className="col" style={{ gap: 12 }} data-testid="ledger-entry">
      <Card title={<span className="mono">{form.id}</span>} sub={`${form.status}${form.owner ? ` · ${form.owner}` : ""} · created ${fmtAgo(form.created_at)}`}
        right={<>
          {form.baseline && form.candidates.length > 0 && <button className="btn sm" onClick={() => nav(`/compare?baseline=${encodeURIComponent(form.baseline!)}&candidate=${encodeURIComponent(form.candidates[0]!)}${form.candidates.length > 1 ? `&more=${encodeURIComponent(form.candidates.slice(1).join(","))}` : ""}`)}><GitCompare size={13} /> Compare</button>}
          <button className="btn sm" disabled={!q.data!.baseline || !q.data!.candidates.length || refresh.isPending} onClick={() => refresh.mutate()} title="Re-compute the outcome (e.g. after the candidate finished)"><RefreshCw size={13} className={refresh.isPending ? "spin" : ""} /> Refresh outcome</button>
          <button className="btn sm" disabled={!q.data!.decision} onClick={() => promote.mutate()} title="Decided entries become a proposed knowledge entry"><BookMarked size={13} /> To knowledge</button>
          <button className="btn sm ghost icon" aria-label="Delete entry" title="Delete" onClick={() => { later(`ledger:${form.id}`, `Deleted ${form.id}`, () => del.mutate()); onDeleted(); }}><Trash2 size={13} /></button>
        </>}>
        <div className="col" style={{ gap: 10 }}>
          <label className="field">Title<input className="input" aria-label="Title" value={form.title} onChange={(e) => set("title", e.target.value)} /></label>
          <label className="field">Hypothesis — what should happen, and why<textarea className="input" aria-label="Hypothesis" rows={2} value={form.hypothesis} onChange={(e) => set("hypothesis", e.target.value)} placeholder="e.g. a second radar FPN level improves recall beyond 60 m" /></label>
          <label className="field">Change<textarea className="input" aria-label="Change" rows={2} value={form.change} onChange={(e) => set("change", e.target.value)} placeholder={form.change_auto ? `from the config diff: ${form.change_auto}` : "what differs from the baseline"} /></label>
          <div className="row wrap" style={{ gap: 12, alignItems: "flex-end" }}>
            <label className="field">Baseline<SubjectPicker value={form.baseline} onChange={(v) => set("baseline", v)} placeholder="Baseline run…" width={280} label="Baseline" /></label>
            <label className="field">Candidate<SubjectPicker value={form.candidates[0] ?? null} onChange={(v) => set("candidates", [v, ...form.candidates.slice(1)].filter((x): x is string => !!x))} placeholder="Candidate run…" width={280} label="Candidate" /></label>
            {form.candidates.slice(1).map((c) => <Pill key={c} tone="outline">+ {short(c)}</Pill>)}
          </div>
          <label className="field">Tags (comma-separated)<input className="input" aria-label="Tags" value={form.tags.join(", ")} onChange={(e) => set("tags", e.target.value.split(",").map((t) => t.trim()).filter(Boolean))} /></label>
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="faint">Decision</span>
            {DECISIONS.map((d) => <button key={d} type="button" className={`btn sm ${form.decision === d ? "primary" : ""}`} aria-pressed={form.decision === d} onClick={() => set("decision", form.decision === d ? "" : d)}>{d}</button>)}
          </div>
          <label className="field">Decision note — what we learned<textarea className="input" aria-label="Decision note" rows={2} value={form.decision_note} onChange={(e) => set("decision_note", e.target.value)} /></label>
          <label className="field">Notes<textarea className="input" aria-label="Notes" rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} /></label>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary" disabled={!dirty || save.isPending} onClick={() => save.mutate(form)}>{save.isPending ? "Saving…" : runsChanged ? "Save and analyse" : "Save"}</button>
            {dirty && <button className="btn ghost" onClick={() => setForm(q.data!)}>Discard changes</button>}
          </div>
        </div>
      </Card>
      {Object.values(q.data!.outcome).map((o) => <OutcomeCard key={o.candidate} o={o} baseline={q.data!.baseline} />)}
      {q.data!.history.length > 0 && (
        <Card title="History" fold="ledger.history" defaultFolded>
          <div className="col" style={{ gap: 2, fontSize: 12 }}>{[...q.data!.history].reverse().map((h, i) => <span key={i} className="faint">{fmtAgo(h.at)} · {h.by} · {h.what}</span>)}</div>
        </Card>
      )}
    </div>
  );
}

function OutcomeCard({ o, baseline }: { o: LedgerOutcome; baseline: string | null }) {
  if (o.error) return <Card title={`Outcome · ${short(o.candidate)}`}><div className="note warn">Could not compute: {o.error}</div></Card>;
  return (
    <Card title={`Outcome · ${short(o.candidate)}`} sub={`vs ${baseline ? short(baseline) : "—"} · ${o.improved} improved · ${o.regressed} regressed · significance: ${o.significance} · computed ${fmtAgo(o.computed_at)}`} fold="ledger.outcome" flush>
      <div className="ledger-outcome" data-testid="ledger-outcome">
        <div>
          <div className="sub-h">Metrics that moved</div>
          {o.moved.length === 0 ? <div className="faint">Nothing moved beyond noise.</div> : (
            <table className="t compact"><tbody>
              {o.moved.map((m) => (
                <tr key={m.metric}>
                  <td className="mono trunc" style={{ maxWidth: 260 }} title={m.metric}>{m.metric}</td>
                  <td className="r num">{fmtNum(m.baseline)}</td>
                  <td className="r num">{fmtNum(m.candidate)}</td>
                  <td className="r num">{m.relative === null ? "" : `${m.relative > 0 ? "+" : ""}${(m.relative * 100).toFixed(1)}%`}</td>
                  <td><Pill tone={m.verdict === "improved" ? "good" : "bad"}>{m.verdict}</Pill></td>
                </tr>
              ))}
            </tbody></table>
          )}
        </div>
        <div>
          <div className="sub-h">Config changes ({o.config_changes.length})</div>
          <div className="col" style={{ gap: 2, fontSize: 12 }}>
            {o.config_changes.slice(0, 12).map((c) => <span key={c.path} className="mono trunc" title={c.text}><span className="faint">{c.category} · </span>{c.text}</span>)}
            {o.config_changes.length === 0 && <span className="faint">no meaningful differences</span>}
          </div>
          <div className="sub-h" style={{ marginTop: 10 }}>Training health problems ({o.problems.length})</div>
          <div className="col" style={{ gap: 4, fontSize: 12 }}>
            {o.problems.map((p) => <span key={p.id}><Pill tone={p.status === "bad" ? "bad" : "warn"}>{p.status === "bad" ? "problem" : "look"}</Pill> <b>{p.question}</b> <span className="faint">{p.answer}</span></span>)}
            {o.problems.length === 0 && <span className="faint">none</span>}
          </div>
        </div>
      </div>
    </Card>
  );
}
