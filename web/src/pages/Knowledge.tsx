import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookMarked, Check, Download, Pencil, Plus, Trash2, Upload, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { KnowledgeEntry } from "../api/types";
import { Card, Empty, ErrorBox, Loading, Pill } from "../components/ui";

const csv = (xs: string[] | undefined): string => (xs ?? []).join(", ");
const list = (s: string): string[] => s.split(",").map((x) => x.trim()).filter(Boolean);
const KIND_TONE: Record<string, string> = { constraint: "warn", fact: "info", setup: "accent", caveat: "outline", code_ref: "outline" };
const BLANK: KnowledgeEntry = { id: "", kind: "constraint", statement: "", why: "", when: {}, effects: {}, source: {}, confidence: "medium", status: "active" };

/** The team's knowledge — independent of runs. Every report applies the active entries and names the entry whenever
 *  it sets something aside. Entries from the harness arrive as proposed. */
export function KnowledgePage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["knowledge"], queryFn: api.knowledge });
  const [edit, setEdit] = useState<KnowledgeEntry | null>(null);
  const [imp, setImp] = useState(false);
  const save = useMutation({ mutationFn: (e: KnowledgeEntry) => api.knowledgeSave(e), onSuccess: () => { setEdit(null); void qc.invalidateQueries({ queryKey: ["knowledge"] }); } });
  const del = useMutation({ mutationFn: (id: string) => api.knowledgeDelete(id), onSuccess: () => void qc.invalidateQueries({ queryKey: ["knowledge"] }) });
  if (q.isLoading) return <div className="page"><Loading /></div>;
  if (q.error) return <div className="page"><ErrorBox error={q.error} /></div>;
  const entries = q.data?.entries ?? [];
  const groups: [string, KnowledgeEntry[]][] = [
    ["Proposed — review before they apply", entries.filter((e) => e.status === "proposed")],
    ["Active", entries.filter((e) => e.status === "active")],
    ["Retired", entries.filter((e) => e.status === "retired")],
  ];
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <div className="page-head">
        <div><h1 className="row"><BookMarked size={20} /> Knowledge</h1>
          <div className="sub">What the team knows, independent of any run. Reports apply active entries and say which entry set something aside. Stored in <code>{q.data?.file}</code> (git-ignored).</div></div>
        <div className="actions">
          <button className="btn" onClick={() => setImp(!imp)}><Upload size={14} /> Import from the harness</button>
          <button className="btn primary" onClick={() => setEdit({ ...BLANK })}><Plus size={14} /> New entry</button>
        </div>
      </div>
      {q.data?.notes.map((n) => <div key={n} className="note warn">{n}</div>)}
      {imp && <ImportBox onDone={() => { setImp(false); void qc.invalidateQueries({ queryKey: ["knowledge"] }); }} />}
      {edit && <EntryEditor entry={edit} onCancel={() => setEdit(null)} onSave={(e) => save.mutate(e)} error={save.error} />}
      {entries.length === 0 && !edit && <Card><Empty icon={<BookMarked size={28} />} title="No knowledge yet">Add an entry (e.g. “manual labels have no velocity”), or copy <code>config/knowledge/knowledge.example.yaml</code> to <code>knowledge.local.yaml</code>.</Empty></Card>}
      {groups.map(([title, es]) => es.length > 0 && (
        <div key={title} style={{ marginTop: 14 }}>
          <div className="sub-h">{title} · {es.length}</div>
          <div className="col" style={{ gap: 8 }}>
            {es.map((e) => (
              <div key={e.id} className="card knowledge-card">
                <div className="row wrap" style={{ gap: 6 }}>
                  <Pill tone={KIND_TONE[e.kind] ?? "outline"}>{e.kind}</Pill>
                  <b>{e.statement}</b>
                  <span className="faint mono" style={{ fontSize: 11 }}>{e.id}</span>
                  <span className="spacer" />
                  <Pill tone="outline">confidence {e.confidence}</Pill>
                  {e.status === "proposed" && <>
                    <button className="btn sm primary" onClick={() => save.mutate({ ...e, status: "active" })}><Check size={13} /> Accept</button>
                    <button className="btn sm" onClick={() => { if (confirm(`Reject and delete ${e.id}?`)) del.mutate(e.id); }}><X size={13} /> Reject</button>
                  </>}
                  {e.status === "active" && <button className="btn sm ghost" onClick={() => save.mutate({ ...e, status: "retired" })}>Retire</button>}
                  {e.status === "retired" && <button className="btn sm ghost" onClick={() => save.mutate({ ...e, status: "active" })}>Reactivate</button>}
                  <button className="btn sm ghost icon" title="Edit" onClick={() => setEdit(e)}><Pencil size={13} /></button>
                  <button className="btn sm ghost icon" title="Delete" onClick={() => { if (confirm(`Delete ${e.id}?`)) del.mutate(e.id); }}><Trash2 size={13} /></button>
                </div>
                {e.why && <div className="muted" style={{ marginTop: 4 }}>{e.why}</div>}
                <div className="row wrap" style={{ gap: 6, marginTop: 6, fontSize: 12 }}>
                  <span className="faint">applies</span>
                  {whenChips(e).length === 0 ? <span className="tag">always</span> : whenChips(e).map((c) => <span key={c} className="tag">{c}</span>)}
                  <span className="faint" style={{ marginLeft: 8 }}>effect</span>
                  {effectChips(e).map((c) => <span key={c} className="tag">{c}</span>)}
                </div>
                {e.effects.caveat && <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>“{e.effects.caveat}”</div>}
                {(e.source.code_refs?.length || e.source.evidence || e.source.via) ? (
                  <div className="faint mono" style={{ fontSize: 11, marginTop: 4 }}>
                    {e.source.via && <span>via {e.source.via} · </span>}{e.source.by && <span>{e.source.by} · </span>}{e.source.at}
                    {e.source.evidence && <span> · {e.source.evidence}</span>}
                    {e.source.code_refs?.map((r) => <div key={r}>↳ {r}</div>)}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function whenChips(e: KnowledgeEntry): string[] {
  const w = e.when ?? {};
  return [
    ...(w.setup ?? []).map((x) => `setup ${x}`),
    ...(w.dataset_contains ?? []).map((x) => `dataset ∋ ${x}`),
    ...(w.stage_contains ?? []).map((x) => `stage ∋ ${x}`),
    ...Object.entries(w.config ?? {}).map(([k, v]) => `${k} = ${v}`),
    ...Object.entries(w.tags ?? {}).map(([k, v]) => `tag ${k}=${v}`),
  ];
}
function effectChips(e: KnowledgeEntry): string[] {
  const f = e.effects ?? {};
  return [
    ...(f.ignore_metrics ?? []).map((x) => `set aside metric ${x}`),
    ...(f.ignore_classes ?? []).map((x) => `set aside class ${x}`),
    ...(f.setup ? [`setup = ${f.setup}`] : []),
    ...(f.caveat && !(f.ignore_metrics?.length || f.ignore_classes?.length) ? ["caveat"] : []),
  ];
}

function EntryEditor({ entry, onSave, onCancel, error }: { entry: KnowledgeEntry; onSave: (e: KnowledgeEntry) => void; onCancel: () => void; error: unknown }) {
  const [e, setE] = useState<KnowledgeEntry>(entry);
  const [cfg, setCfg] = useState(Object.entries(entry.when?.config ?? {}).map(([k, v]) => `${k} = ${v}`).join("\n"));
  const w = e.when ?? {};
  const f = e.effects ?? {};
  const submit = (): void => {
    const config = Object.fromEntries(cfg.split("\n").map((l) => l.split("=")).filter((p) => p.length >= 2 && p[0]!.trim()).map((p) => [p[0]!.trim(), p.slice(1).join("=").trim()]));
    onSave({ ...e, when: { ...w, config } });
  };
  return (
    <Card title={entry.id ? `Edit ${entry.id}` : "New knowledge entry"} right={<button className="btn ghost sm icon" onClick={onCancel}><X size={14} /></button>}>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
        <label className="field">Id<input className="input mono" value={e.id} disabled={!!entry.id} placeholder="K-manual-labels-no-velocity" onChange={(x) => setE({ ...e, id: x.target.value })} /></label>
        <label className="field">Kind<select className="select" value={e.kind} onChange={(x) => setE({ ...e, kind: x.target.value as KnowledgeEntry["kind"] })}>{["constraint", "fact", "setup", "caveat", "code_ref"].map((k) => <option key={k}>{k}</option>)}</select></label>
        <label className="field">Confidence<select className="select" value={e.confidence} onChange={(x) => setE({ ...e, confidence: x.target.value as KnowledgeEntry["confidence"] })}>{["low", "medium", "high"].map((k) => <option key={k}>{k}</option>)}</select></label>
      </div>
      <label className="field">Statement<input className="input" value={e.statement} placeholder="Manual labels carry no velocity or acceleration ground truth." onChange={(x) => setE({ ...e, statement: x.target.value })} /></label>
      <label className="field">Why<textarea className="input" rows={2} value={e.why} onChange={(x) => setE({ ...e, why: x.target.value })} /></label>
      <div className="sub-h">Applies when (all given conditions; leave empty = always)</div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
        <label className="field">Dataset name contains<input className="input" value={csv(w.dataset_contains)} placeholder="manual, gt_" onChange={(x) => setE({ ...e, when: { ...w, dataset_contains: list(x.target.value) } })} /></label>
        <label className="field">Stage contains<input className="input" value={csv(w.stage_contains)} placeholder="finetune" onChange={(x) => setE({ ...e, when: { ...w, stage_contains: list(x.target.value) } })} /></label>
        <label className="field">Setup<input className="input" value={csv(w.setup)} placeholder="1v5r, 5v1L" onChange={(x) => setE({ ...e, when: { ...w, setup: list(x.target.value) } })} /></label>
      </div>
      <label className="field">Config (one per line: key glob = value glob)<textarea className="input mono" rows={2} value={cfg} placeholder="*num_radars = 5" onChange={(x) => setCfg(x.target.value)} /></label>
      <div className="sub-h">Effect</div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
        <label className="field">Set aside metrics (globs)<input className="input mono" value={csv(f.ignore_metrics)} placeholder="*velocity*, *accel*" onChange={(x) => setE({ ...e, effects: { ...f, ignore_metrics: list(x.target.value) } })} /></label>
        <label className="field">Set aside classes<input className="input" value={csv(f.ignore_classes)} placeholder="Animal" onChange={(x) => setE({ ...e, effects: { ...f, ignore_classes: list(x.target.value) } })} /></label>
        {e.kind === "setup" && <label className="field">Setup label<input className="input" value={f.setup ?? ""} placeholder="1v5r" onChange={(x) => setE({ ...e, effects: { ...f, setup: x.target.value } })} /></label>}
      </div>
      <label className="field">Caveat shown where it applies<input className="input" value={f.caveat ?? ""} onChange={(x) => setE({ ...e, effects: { ...f, caveat: x.target.value } })} /></label>
      <label className="field">Code references (file:line, comma separated)<input className="input mono" value={csv(e.source?.code_refs)} onChange={(x) => setE({ ...e, source: { ...e.source, code_refs: list(x.target.value) } })} /></label>
      {error ? <ErrorBox error={error} /> : null}
      <div className="row" style={{ gap: 8, marginTop: 8 }}><span className="spacer" /><button className="btn" onClick={onCancel}>Cancel</button>
        <button className="btn primary" disabled={!e.id || e.statement.length < 3} onClick={submit}><Check size={14} /> Save</button></div>
    </Card>
  );
}

function ImportBox({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");
  const [res, setRes] = useState<{ added: string[]; errors: string[] } | null>(null);
  return (
    <Card title={<span className="row"><Download size={14} /> Import entries (harness output)</span>} sub="validated against the schema; added as proposed — nothing applies until you accept it">
      <textarea className="input mono" rows={8} placeholder={"entries:\n  - id: K-...\n    kind: fact\n    statement: ..."} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row" style={{ gap: 8, marginTop: 8 }}>
        {res && <span className={res.errors.length ? "faint" : ""}>{res.added.length} added{res.errors.length ? ` · ${res.errors.length} problem(s)` : ""}</span>}
        <span className="spacer" />
        <button className="btn" onClick={onDone}>Close</button>
        <button className="btn primary" disabled={!text.trim()} onClick={() => void api.knowledgeImport(text).then((r) => { setRes(r); if (!r.errors.length) { setText(""); onDone(); } })}><Upload size={14} /> Import</button>
      </div>
      {res?.errors.map((e) => <div key={e} className="faint mono" style={{ fontSize: 12 }}>{e}</div>)}
    </Card>
  );
}
