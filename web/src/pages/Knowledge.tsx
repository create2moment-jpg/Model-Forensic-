import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookMarked, Check, Lightbulb, Download, FileText, FolderTree, Pencil, Plus, RefreshCw, Search, Trash2, Upload, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { MarkdownView } from "../components/MarkdownView";
import { ExperienceTab } from "./knowledge/Experience";
import { useDebounced } from "../lib/hooks";
import { RunPicker } from "../components/RunPicker";
import { useApp } from "../components/AppState";
import { CodeRef } from "../components/CodeRef";
import { api } from "../api/client";
import type { KnowledgeEntry, SourceReport } from "../api/types";
import { Card, Empty, ErrorBox, Loading, Pill, Tabs } from "../components/ui";

const csv = (xs: string[] | undefined): string => (xs ?? []).join(", ");
const list = (s: string): string[] => s.split(",").map((x) => x.trim()).filter(Boolean);
const KIND_TONE: Record<string, string> = { constraint: "warn", fact: "info", setup: "accent", caveat: "outline", code_ref: "outline" };
const BLANK: KnowledgeEntry = { id: "", kind: "constraint", statement: "", why: "", when: {}, effects: {}, source: {}, confidence: "medium", status: "active" };

/** The team's knowledge — independent of runs. Every report applies the active entries and names the entry whenever
 *  it sets something aside. Entries from the harness arrive as proposed. */
export function KnowledgePage() {
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as "entries" | "experience" | "docs" | "sources" | null) ?? "entries";
  const bel = useQuery({ queryKey: ["beliefs"], queryFn: api.beliefs });
  const k = useQuery({ queryKey: ["knowledge"], queryFn: api.knowledge });
  const src = useQuery({ queryKey: ["knowledge-sources"], queryFn: api.knowledgeSources });
  const docs = (src.data?.sources ?? []).reduce((a, s) => a + s.docs, 0);
  return (
    <div className="page narrow" style={{ maxWidth: 1200 }}>
      <div className="page-head">
        <div><h1>Knowledge</h1>
          <div className="sub">What the team knows, independent of any run — entries that reports apply, and the documentation of every codebase you connect. Your coding agents write to one inbox: <code>{src.data?.inbox ?? k.data?.folder}</code>.</div></div>
      </div>
      <Tabs value={tab} onChange={(t) => setSp((p) => { const n = new URLSearchParams(p); n.set("tab", t); n.delete("doc"); return n; }, { replace: true })} tabs={[
        { id: "entries", label: "Entries", icon: <BookMarked size={14} />, count: k.data?.entries.filter((e) => e.kind !== "belief").length },
        { id: "experience", label: "Experience", icon: <Lightbulb size={14} />, count: bel.data?.beliefs.length },
        { id: "docs", label: "Documentation", icon: <FileText size={14} />, count: src.data ? docs : undefined },
        { id: "sources", label: "Sources", icon: <FolderTree size={14} />, count: src.data?.sources.length },
      ]} />
      {tab === "entries" && <EntriesTab />}
      {tab === "experience" && <ExperienceTab />}
      {tab === "docs" && <DocsTab />}
      {tab === "sources" && <SourcesTab />}
    </div>
  );
}

function EntriesTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["knowledge"], queryFn: api.knowledge });
  const [edit, setEdit] = useState<KnowledgeEntry | null>(null);
  const [imp, setImp] = useState(false);
  const save = useMutation({ mutationFn: (e: KnowledgeEntry) => api.knowledgeSave(e), onSuccess: () => { setEdit(null); void qc.invalidateQueries({ queryKey: ["knowledge"] }); } });
  const { later, pending } = useApp();
  const del = useMutation({ mutationFn: (id: string) => api.knowledgeDelete(id), onSuccess: () => void qc.invalidateQueries({ queryKey: ["knowledge"] }) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const entries = (q.data?.entries ?? []).filter((e) => e.kind !== "belief");
  const groups: [string, KnowledgeEntry[]][] = [
    ["Proposed — review before they apply", entries.filter((e) => e.status === "proposed")],
    ["Active", entries.filter((e) => e.status === "active")],
    ["Retired", entries.filter((e) => e.status === "retired")],
  ];
  return (
    <div>
      <div className="row wrap" style={{ gap: 8, margin: "4px 0 10px" }}>
        <div className="muted" style={{ fontSize: 13 }}>Reports apply active entries and say which entry set something aside. Your edits go to <code>{q.data?.file}</code> (git-ignored); entries from other sources are read-only — accepting or retiring one writes an override there.</div>
        <span className="spacer" />
        <button className="btn" onClick={() => setImp(!imp)}><Upload size={14} /> Import from the harness</button>
        <button className="btn primary" onClick={() => setEdit({ ...BLANK })}><Plus size={14} /> New entry</button>
      </div>
      {q.data?.notes.map((n) => <div key={n} className="note warn">{n}</div>)}
      <HarnessPanel />
      {imp && <ImportBox onDone={() => { setImp(false); void qc.invalidateQueries({ queryKey: ["knowledge"] }); }} />}
      {edit && <EntryEditor entry={edit} onCancel={() => setEdit(null)} onSave={(e) => save.mutate(e)} error={save.error} />}
      {entries.length === 0 && !edit && <Card><Empty icon={<BookMarked size={28} />} title="No knowledge yet">Add an entry (e.g. “manual labels have no velocity”), or copy <code>config/knowledge/knowledge.example.yaml</code> to <code>knowledge.local.yaml</code>.</Empty></Card>}
      {groups.map(([title, es]) => es.length > 0 && (
        <div key={title} style={{ marginTop: 14 }}>
          <div className="sub-h">{title} · {es.length}</div>
          <div className="col" style={{ gap: 8 }}>
            {es.filter((e) => !pending(`knowledge:${e.id}`)).map((e) => (
              <div key={e.id} className="card knowledge-card">
                <div className="row wrap" style={{ gap: 6 }}>
                  <Pill tone={KIND_TONE[e.kind] ?? "outline"}>{e.kind}</Pill>
                  <b>{e.statement}</b>
                  <span className="faint mono" style={{ fontSize: 11 }}>{e.id}</span>
                  <span className="spacer" />
                  <Pill tone="outline">confidence {e.confidence}</Pill>
                  {e.status === "proposed" && <>
                    <button className="btn sm primary" onClick={() => save.mutate({ ...e, status: "active" })}><Check size={13} /> Accept</button>
                    <button className="btn sm" onClick={() => later(`knowledge:${e.id}`, `Rejected ${e.id}`, () => del.mutate(e.id))}><X size={13} /> Reject</button>
                  </>}
                  {e.status === "active" && <button className="btn sm ghost" onClick={() => save.mutate({ ...e, status: "retired" })}>Retire</button>}
                  {e.status === "retired" && <button className="btn sm ghost" onClick={() => save.mutate({ ...e, status: "active" })}>Reactivate</button>}
                  <button className="btn sm ghost icon" title="Edit" onClick={() => setEdit(e)}><Pencil size={13} /></button>
                  <button className="btn sm ghost icon" title="Delete" onClick={() => later(`knowledge:${e.id}`, `Deleted ${e.id}`, () => del.mutate(e.id))}><Trash2 size={13} /></button>
                </div>
                {e.why && <div className="muted" style={{ marginTop: 4 }}>{e.why}</div>}
                {e.origin && !e.origin.endsWith("knowledge.local.yaml") && <div className="faint mono" style={{ fontSize: 11, marginTop: 2 }}>from {e.origin}</div>}
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
                    {e.source.code_refs?.map((r) => <div key={r}>↳ <CodeRef refText={r} /></div>)}
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

/** Brief for a coding agent inside the training project: what a run shows + a task; its answer is imported above. */
function HarnessPanel() {
  const tasks = useQuery({ queryKey: ["harness-tasks"], queryFn: api.harnessTasks });
  const [run, setRun] = useState<string | null>(null);
  const [task, setTask] = useState("loss_composition");
  return (
    <details className="card" style={{ padding: "10px 14px", marginTop: 10 }}>
      <summary><b>Learn from the codebase (harness)</b> <span className="faint">— a brief for your coding agent inside the training project; import its answer here</span></summary>
      <ol className="muted" style={{ margin: "8px 0 8px 18px", padding: 0, fontSize: 13 }}>
        <li>Pick a run and a task, download the brief.</li>
        <li>Give it to the coding agent (e.g. Copilot) inside the training project — it reads the code and answers with knowledge YAML.</li>
        <li><b>Import</b> the answer above; review the proposed entries and accept them.</li>
      </ol>
      <div className="row wrap" style={{ gap: 8 }}>
        <RunPicker value={run} onChange={setRun} placeholder="Run the brief is about…" width={300} />
        <select className="select" aria-label="Harness task" value={task} onChange={(e) => setTask(e.target.value)}>
          {Object.entries(tasks.data ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <a className={`btn primary ${run ? "" : "disabled"}`} href={run ? api.harnessBriefUrl(run, task) : undefined} download={`brief-${task}.md`}><Download size={14} /> Download brief</a>
      </div>
    </details>
  );
}

function Snippet({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]]*\])/g);
  return <span>{parts.map((p, i) => p.startsWith("[") && p.endsWith("]") ? <mark key={i}>{p.slice(1, -1)}</mark> : <span key={i}>{p}</span>)}</span>;
}

/** Full-text search over the documentation of every source; a hit opens the document at its section. */
function DocsTab() {
  const [sp, setSp] = useSearchParams();
  const [text, setText] = useState(sp.get("q") ?? "");
  const [source, setSource] = useState<string>("");
  const query = useDebounced(text.trim(), 250);
  const open = sp.get("doc");
  const sources = useQuery({ queryKey: ["knowledge-sources"], queryFn: api.knowledgeSources });
  const hits = useQuery({ queryKey: ["docs-search", query, source], queryFn: () => api.docsSearch(query, source || undefined), enabled: query.length >= 2 });
  const files = useQuery({ queryKey: ["docs-files", source], queryFn: () => api.docsFiles(source || undefined), enabled: query.length < 2 });
  const select = (src: string, path: string, anchor = "") =>
    setSp((p) => { const n = new URLSearchParams(p); n.set("doc", `${src}:${path}${anchor ? "#" + anchor : ""}`); if (query) n.set("q", query); return n; });
  return (
    <div className="docs-layout">
      <div className="col" style={{ gap: 8, minWidth: 0 }}>
        <div className="row" style={{ gap: 8 }}>
          <div className="input-icon" style={{ flex: 1 }}><Search size={14} />
            <input className="input" aria-label="Search documentation" placeholder="Search all documentation — e.g. uncertainty sigma, manual labels velocity" value={text} onChange={(e) => setText(e.target.value)} /></div>
          <select className="input" style={{ width: 170 }} aria-label="Source" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">all sources</option>
            {sources.data?.sources.map((s) => <option key={s.name} value={s.name}>{s.name} ({s.docs})</option>)}
          </select>
        </div>
        {query.length >= 2 ? (
          hits.isLoading ? <Loading /> : hits.error ? <ErrorBox error={hits.error} /> : hits.data!.hits.length === 0 ? <div className="muted">No section mentions all of these words.</div> : (
            <div className="col" style={{ gap: 6 }}>
              {hits.data!.hits.map((h) => (
                <button key={`${h.source}:${h.path}#${h.anchor}`} className={`doc-hit ${open === `${h.source}:${h.path}${h.anchor ? "#" + h.anchor : ""}` ? "on" : ""}`} onClick={() => select(h.source, h.path, h.anchor)}>
                  <div className="row" style={{ gap: 6 }}><b>{h.heading}</b><span className="spacer" />{h.codebase && <Pill tone="outline">{h.codebase}</Pill>}</div>
                  <div className="faint mono" style={{ fontSize: 11 }}>{h.source}:{h.path}</div>
                  <div className="muted" style={{ fontSize: 12.5 }}><Snippet text={h.snippet} /></div>
                </button>
              ))}
            </div>
          )
        ) : files.isLoading ? <Loading /> : (files.data?.files.length ?? 0) === 0 ? (
          <Card><Empty icon={<FileText size={28} />} title="No documentation indexed yet">Connect folders in the <b>Sources</b> tab, or let your coding agent write Markdown to <code>{sources.data?.inbox}/docs/</code>.</Empty></Card>
        ) : (
          <div className="col" style={{ gap: 4 }}>
            <div className="sub-h">Recently changed · {files.data!.files.length}</div>
            {files.data!.files.map((f) => (
              <button key={`${f.source}:${f.path}`} className={`doc-hit ${open === `${f.source}:${f.path}` ? "on" : ""}`} onClick={() => select(f.source, f.path)}>
                <div className="row" style={{ gap: 6 }}><b>{f.title}</b><span className="spacer" /><span className="faint" style={{ fontSize: 11 }}>{new Date(f.mtime * 1000).toLocaleDateString()}</span></div>
                <div className="faint mono" style={{ fontSize: 11 }}>{f.source}:{f.path}</div>
              </button>
            ))}
          </div>
        )}
      </div>
      <div style={{ minWidth: 0 }}>{open ? <DocViewer id={open} /> : <div className="muted" style={{ padding: 20 }}>Pick a result to read it here.</div>}</div>
    </div>
  );
}

function DocViewer({ id }: { id: string }) {
  const [where = "", anchor = ""] = id.split("#");
  const i = where.indexOf(":");
  const source = where.slice(0, i), path = where.slice(i + 1);
  const d = useQuery({ queryKey: ["docs-file", source, path], queryFn: () => api.docsFile(source, path) });
  useEffect(() => {
    if (!d.data || !anchor) return;
    const t = setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ block: "start", behavior: "smooth" }), 50);
    return () => clearTimeout(t);
  }, [d.data, anchor]);
  if (d.isLoading) return <Loading />;
  if (d.error) return <ErrorBox error={d.error} />;
  return (
    <div className="card doc-view">
      <div className="row" style={{ gap: 6, marginBottom: 6 }}>
        <span className="faint mono" style={{ fontSize: 11 }}>{source}:{path}</span><span className="spacer" />{d.data!.codebase && <Pill tone="outline">{d.data!.codebase}</Pill>}
      </div>
      <MarkdownView text={d.data!.text} />
    </div>
  );
}

function ReindexNote({ r }: { r: SourceReport | undefined }) {
  if (!r) return null;
  return <div className="faint">{r.files} files, {r.changed} changed, {r.removed} removed in {r.seconds}s{r.error ? ` — ${r.error}` : ""}</div>;
}

/** Where knowledge comes from: the inbox (one place for every agent) and one folder per codebase. */
function SourcesTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["knowledge-sources"], queryFn: api.knowledgeSources });
  const re = useMutation({ mutationFn: api.knowledgeReindex, onSuccess: (r) => { qc.setQueryData(["knowledge-sources"], { sources: r.sources, inbox: q.data?.inbox ?? "" }); void qc.invalidateQueries({ queryKey: ["docs-files"] }); void qc.invalidateQueries({ queryKey: ["knowledge"] }); } });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const inbox = q.data!.inbox;
  const reports = Object.fromEntries((re.data?.reports ?? []).map((r) => [r.source, r]));
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row"><span className="muted" style={{ fontSize: 13 }}>Files stay the source of truth — Model Forensic reads them, re-reading only what changed.</span><span className="spacer" />
        <button className="btn" onClick={() => re.mutate()} disabled={re.isPending}><RefreshCw size={14} className={re.isPending ? "spin" : ""} /> Re-index now</button></div>
      <table className="tbl">
        <thead><tr><th>Source</th><th>Folder</th><th>Reads</th><th>Trust</th><th style={{ textAlign: "right" }}>Entries</th><th style={{ textAlign: "right" }}>Docs</th></tr></thead>
        <tbody>
          {q.data!.sources.map((s) => (
            <tr key={s.name}>
              <td><b>{s.name}</b> {s.inbox && <Pill tone="accent">inbox</Pill>}{s.codebase && !s.inbox && <div className="faint" style={{ fontSize: 11 }}>{s.codebase}</div>}</td>
              <td className="mono" style={{ fontSize: 12 }}>
                <span className={s.exists ? "" : "bad"}>{s.resolved ?? s.path}</span>{!s.exists && <span className="bad"> — {s.resolved ? "not found" : "unset variable"}</span>}
                {s.env && <div className="faint">from ${s.env}</div>}
                <ReindexNote r={reports[s.name]} />
                {s.problems.slice(0, 3).map((p) => <div key={p} className="warn-text">{p}</div>)}
              </td>
              <td>{s.kind === "auto" ? "entries + docs" : s.kind}</td>
              <td>{s.trust === "proposed" ? <span title="Entries apply only after someone accepts them">review first</span> : "as written"}</td>
              <td style={{ textAlign: "right" }}>{s.entries}</td>
              <td style={{ textAlign: "right" }}>{s.docs}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="grid g2">
        <Card title="Connect another codebase">
          <div className="muted" style={{ fontSize: 13 }}>One environment variable per documentation folder (name it after the codebase):</div>
          <pre className="md-code">{`export MF_KNOWLEDGE_SOURCE_PERCEPTION=~/work/perception/docs
export MF_KNOWLEDGE_SOURCE_DATA_TOOLS=~/work/data-tools`}</pre>
          <div className="muted" style={{ fontSize: 13 }}>or, with options, in <code>model-forensic.yaml</code>:</div>
          <pre className="md-code">{`knowledge_sources:
  - name: perception
    path: \${PERCEPTION_REPO}/docs   # \${VAR} and ~ work
    codebase: perception
    trust: proposed              # agent-written: review before entries apply
    exclude: ["archive/*"]`}</pre>
        </Card>
        <Card title="Tell your coding agents where to write">
          <div className="muted" style={{ fontSize: 13 }}>Put this in each codebase's agent instructions (e.g. <code>.github/copilot-instructions.md</code>) so knowledge always accumulates in one place:</div>
          <pre className="md-code" style={{ whiteSpace: "pre-wrap" }}>{`Write what you learn about the training code as Model Forensic knowledge YAML to
${inbox}/<topic>-<yyyy-mm-dd>.yaml (schema: harness/knowledge.schema.json,
status: proposed, cite file:line in source.code_refs) and longer explanations as
Markdown to ${inbox}/docs/<codebase>/<topic>.md. Never write elsewhere.`}</pre>
          <div className="faint" style={{ fontSize: 12 }}>Move the inbox with <code>MF_KNOWLEDGE_INBOX</code>. CLI: <code>mf knowledge where</code>, <code>mf knowledge sources</code>, <code>mf docs search …</code>.</div>
        </Card>
      </div>
    </div>
  );
}
