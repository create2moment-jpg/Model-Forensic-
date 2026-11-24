import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FlaskConical, Lightbulb, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../../api/client";
import type { BeliefColour, BeliefItem, BeliefTest, KnowledgeEntry } from "../../api/types";
import { useApp } from "../../components/AppState";
import { Card, Empty, ErrorBox, Loading, Pill } from "../../components/ui";
import { fmtAgo } from "../../lib/format";

export const COLOUR: Record<BeliefColour, { bg: string; label: string; meaning: string }> = {
  dark_green: { bg: "#137a3f", label: "well supported", meaning: "≥ 85 % likely, a tight interval and enough clean comparisons" },
  green: { bg: "#2fb37e", label: "supported", meaning: "the runs lean in its favour" },
  yellow: { bg: "#d9a520", label: "uncertain", meaning: "mixed or thin evidence — see what would settle it" },
  red: { bg: "#e0565b", label: "contradicted", meaning: "the runs mostly argue against it" },
  grey: { bg: "#8a8f98", label: "not checked", meaning: "no test yet, or no usable comparison in its scope" },
};
const ORDER: BeliefColour[] = ["dark_green", "green", "yellow", "red", "grey"];

export function Bubble({ colour, size = 12 }: { colour: BeliefColour; size?: number }) {
  return <span className="bubble" title={COLOUR[colour].label} style={{ background: COLOUR[colour].bg, width: size, height: size }} />;
}

const slug = (s: string): string => "B-" + s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
const BLANK_TEST: BeliefTest = { type: "config_effect", key: "", change: "increase", value: "", metric: "", expect: "up", min_effect: 0.01, family: "", behaviour: "muted", scope: "", max_runs: 40 };

function testText(t: BeliefTest | null | undefined): string {
  if (!t) return "no test yet";
  if (t.type === "loss_behaviour") return `loss family ${t.family} is ${String(t.behaviour).replace(/_/g, " ")}`;
  const ch = t.change === "set" ? `${t.key} = ${t.value}` : `${t.change === "increase" ? "↑" : "↓"} ${t.key}`;
  return `${ch} → ${t.metric} ${t.expect === "up" ? "↑" : t.expect === "down" ? "↓" : "unchanged"}`;
}

/** Knowledge from experience: what you believe, checked against the runs. Every belief gets a colour and a
 *  probability; nothing here is treated as fact. */
export function ExperienceTab() {
  const qc = useQueryClient();
  const { trackJob } = useApp();
  const q = useQuery({ queryKey: ["beliefs"], queryFn: api.beliefs });
  const [edit, setEdit] = useState<KnowledgeEntry | null>(null);
  const [filter, setFilter] = useState<BeliefColour | "all">("all");
  const verifyAll = useMutation({ mutationFn: api.verifyAllBeliefs, onSuccess: (r) => trackJob(r.job_id) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const items = q.data!.beliefs;
  const colourOf = (b: BeliefItem): BeliefColour => (b.verdict?.colour ?? "grey");
  const counts = Object.fromEntries(ORDER.map((c) => [c, items.filter((b) => colourOf(b) === c).length])) as Record<BeliefColour, number>;
  const shown = items
    .filter((b) => filter === "all" || colourOf(b) === filter)
    .sort((a, b) => ORDER.indexOf(colourOf(a)) - ORDER.indexOf(colourOf(b)) || (b.verdict?.p ?? 0.5) - (a.verdict?.p ?? 0.5));
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row wrap" style={{ gap: 8 }}>
        <div className="muted" style={{ fontSize: 13, maxWidth: 760 }}>What you know from experience — written in your words, then <b>checked against the runs</b>. Each belief gets a colour and a probability that it holds; your own confidence counts only as a weak prior. Model Forensic uses them in study books whenever a comparison changes the setting a belief is about.</div>
        <span className="spacer" />
        <button className="btn" disabled={verifyAll.isPending || items.length === 0} onClick={() => verifyAll.mutate()}><RefreshCw size={14} /> Check all</button>
        <button className="btn primary" onClick={() => setEdit({ id: "", kind: "belief", statement: "", why: "", when: {}, effects: {}, source: {}, confidence: "medium", status: "active", test: { ...BLANK_TEST } })}><Plus size={14} /> Add experience</button>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <button className={`filter-chip ${filter === "all" ? "on" : ""}`} onClick={() => setFilter("all")}>all · {items.length}</button>
        {ORDER.map((c) => (
          <button key={c} className={`filter-chip ${filter === c ? "on" : ""}`} title={COLOUR[c].meaning} onClick={() => setFilter(c)}>
            <Bubble colour={c} size={9} /> {COLOUR[c].label} · {counts[c]}
          </button>
        ))}
      </div>
      {edit && <BeliefEditor entry={edit} onClose={() => setEdit(null)} onSaved={(id) => { setEdit(null); void qc.invalidateQueries({ queryKey: ["beliefs"] }); void api.verifyBelief(id).then((r) => trackJob(r.job_id)); }} />}
      {items.length === 0 && !edit ? (
        <Card><Empty icon={<Lightbulb size={28} />} title="No experience recorded yet">
          Write down what you have learnt — e.g. “a higher learning rate hurts the finetune”, “σ mutes the radar head” — and add a test so it can be checked against your runs.
        </Empty></Card>
      ) : (
        <div className="col" style={{ gap: 8 }}>
          {shown.map((b) => <BeliefCard key={b.entry.id} item={b} onEdit={() => setEdit(b.entry)} />)}
        </div>
      )}
    </div>
  );
}

function BeliefCard({ item, onEdit }: { item: BeliefItem; onEdit: () => void }) {
  const qc = useQueryClient();
  const { trackJob, later, pending } = useApp();
  const [open, setOpen] = useState(false);
  const e = item.entry;
  const v = item.verdict;
  const colour = v?.colour ?? "grey";
  const check = useMutation({ mutationFn: () => api.verifyBelief(e.id), onSuccess: (r) => trackJob(r.job_id) });
  const del = useMutation({ mutationFn: () => api.knowledgeDelete(e.id), onSuccess: () => void qc.invalidateQueries({ queryKey: ["beliefs"] }) });
  const retire = useMutation({ mutationFn: () => api.knowledgeSave({ ...e, status: e.status === "retired" ? "active" : "retired" }), onSuccess: () => void qc.invalidateQueries({ queryKey: ["beliefs"] }) });
  if (pending(`knowledge:${e.id}`)) return null;
  return (
    <div className={`card belief-card ${e.status === "retired" ? "retired" : ""}`} data-colour={colour}>
      <div className="row wrap" style={{ gap: 8 }}>
        <Bubble colour={colour} size={14} />
        <b style={{ fontSize: 14 }}>{e.statement}</b>
        {e.status === "retired" && <Pill tone="outline">retired</Pill>}
        {item.stale && <Pill tone="warn">test changed — check again</Pill>}
        <span className="spacer" />
        {v && v.n > 0 && <span className="belief-p" title={`80 % interval ${Math.round(v.lo * 100)}–${Math.round(v.hi * 100)} %`}>
          <span className="belief-bar"><span style={{ left: `${v.lo * 100}%`, width: `${Math.max(1, (v.hi - v.lo) * 100)}%`, background: COLOUR[colour].bg }} /><i style={{ left: `${v.p * 100}%` }} /></span>
          <b>{Math.round(v.p * 100)}%</b>
        </span>}
        <button className="btn sm" disabled={check.isPending || !item.testable} title={item.testable ? "Check against the runs now" : "Add a test first"} onClick={() => check.mutate()}><RefreshCw size={13} /> Check</button>
        <button className="btn sm ghost icon" title="Edit" onClick={onEdit}><Pencil size={13} /></button>
        <button className="btn sm ghost" onClick={() => retire.mutate()}>{e.status === "retired" ? "Reactivate" : "Retire"}</button>
        <button className="btn sm ghost icon" title="Delete" onClick={() => later(`knowledge:${e.id}`, "Deleted the belief", () => del.mutate())}><Trash2 size={13} /></button>
      </div>
      <div className="row wrap faint" style={{ gap: 10, fontSize: 12, marginTop: 4 }}>
        <span className="mono">{testText(e.test)}</span>
        {e.test?.scope && <span>scope: <code>{e.test.scope}</code></span>}
        <span>your confidence: {e.confidence}</span>
        {!item.testable ? <span>no test yet — edit it and add one so it can be checked</span> : v ? <span>{COLOUR[colour].label} · {v.support > 0 || v.contra > 0 ? `${fmtW(v.support)} for, ${fmtW(v.contra)} against` : "no usable comparison"}{v.no_effect ? `, ${v.no_effect} within noise` : ""} · checked {fmtAgo(v.at)}</span> : <span>not checked yet</span>}
      </div>
      {e.why && <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>{e.why}</div>}
      <button className="link-btn" style={{ marginTop: 6 }} onClick={() => setOpen(!open)}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} What Model Forensic thinks</button>
      {open && <BeliefDetailView id={e.id} />}
    </div>
  );
}

const fmtW = (w: number): string => (Number.isInteger(w) ? String(w) : w.toFixed(1));

function BeliefDetailView({ id }: { id: string }) {
  const d = useQuery({ queryKey: ["belief", id], queryFn: () => api.belief(id) });
  if (d.isLoading) return <Loading />;
  if (d.error) return <ErrorBox error={d.error} />;
  const l = d.data!.latest;
  if (!l) return <div className="muted" style={{ padding: "6px 0" }}>Not checked yet — press <b>Check</b>.</div>;
  const v = l.verdict;
  const ev = v.evidence.filter((x) => x.outcome !== "skipped");
  const skipped = v.evidence.filter((x) => x.outcome === "skipped");
  return (
    <div className="belief-detail">
      <p>{v.summary}</p>
      {Math.abs(v.p - v.p_data) >= 0.1 && <p className="faint" style={{ fontSize: 12 }}>From the runs alone: {Math.round(v.p_data * 100)} %. Your stated confidence moves it to {Math.round(v.p * 100)} % — collect evidence to make it independent of who wrote it.</p>}
      {v.next_experiment && <div className="note" style={{ display: "flex", gap: 8 }}><FlaskConical size={14} /><span><b>What would settle it:</b> {v.next_experiment}</span></div>}
      {ev.length > 0 && (
        <table className="tbl" style={{ marginTop: 8 }}>
          <thead><tr><th>Outcome</th><th>Comparison</th><th style={{ textAlign: "right" }}>Weight</th></tr></thead>
          <tbody>{ev.map((x, i) => (
            <tr key={i} className={x.outcome === "contradicts" ? "row-bad" : ""}>
              <td><Pill tone={x.outcome === "supports" ? "good" : x.outcome === "contradicts" ? "bad" : "outline"}>{x.outcome.replace("_", " ")}</Pill></td>
              <td style={{ fontSize: 12.5 }}>{x.detail}</td>
              <td style={{ textAlign: "right" }} title={x.other_diffs ? `${x.other_diffs} other setting(s) differ — counts less` : "clean comparison"}>{x.weight.toFixed(2)}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {skipped.map((x, i) => <div key={i} className="faint" style={{ fontSize: 12 }}>{x.detail}</div>)}
      {l.history.length > 1 && (
        <div className="row wrap" style={{ gap: 4, marginTop: 8, alignItems: "center" }}>
          <span className="faint" style={{ fontSize: 12 }}>History:</span>
          {l.history.map((h, i) => <span key={i} title={`${new Date(h.at).toLocaleString()} — ${Math.round(h.p * 100)} %, ${h.n} comparisons`} className="row" style={{ gap: 2, fontSize: 11 }}><Bubble colour={h.colour} size={9} />{Math.round(h.p * 100)}%</span>)}
        </div>
      )}
      <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>{v.runs_considered} run(s) in scope · effect threshold ±{(v.threshold * 100).toFixed(1)} % · checked {fmtAgo(l.at)}</div>
    </div>
  );
}

function BeliefEditor({ entry, onClose, onSaved }: { entry: KnowledgeEntry; onClose: () => void; onSaved: (id: string) => void }) {
  const [e, setE] = useState<KnowledgeEntry>(entry);
  const [testOn, setTestOn] = useState<boolean>(!!entry.test);
  const t: BeliefTest = e.test ?? { ...BLANK_TEST };
  const setT = (p: Partial<BeliefTest>): void => setE({ ...e, test: { ...t, ...p } });
  const vocab = useQuery({ queryKey: ["belief-vocab", t.scope ?? ""], queryFn: () => api.beliefVocabulary(t.scope ?? ""), enabled: testOn, staleTime: 60_000 });
  const keys = useMemo(() => (vocab.data?.config_keys ?? []).filter((k) => t.change === "set" || k.numeric), [vocab.data, t.change]);
  const save = useMutation({
    mutationFn: () => {
      const id = e.id || slug(e.statement);
      return api.knowledgeSave({ ...e, id, kind: "belief", test: testOn ? t : null, source: { ...e.source, via: e.source.via || "experience" } });
    },
    onSuccess: (r) => onSaved(r.id),
  });
  const ok = e.statement.trim().length >= 3 && (!testOn || (t.type === "config_effect" ? !!t.key && !!t.metric && (t.change !== "set" || !!t.value) : !!t.family));
  return (
    <Card title={entry.id ? `Edit ${entry.id}` : "Add experience"} sub="In your words first; a test makes it checkable">
      <div className="col" style={{ gap: 10 }}>
        <label className="field">What you believe *<input className="input" autoFocus value={e.statement} onChange={(x) => setE({ ...e, statement: x.target.value })} placeholder="A higher learning rate hurts the GT finetune" /></label>
        <label className="field">Where it comes from (optional)<textarea className="input" rows={2} value={e.why} onChange={(x) => setE({ ...e, why: x.target.value })} placeholder="Seen in the spring sweeps; the finetune diverged twice above 4e-4" /></label>
        <div className="row wrap" style={{ gap: 12 }}>
          <label className="field">Your confidence<select className="input" value={e.confidence} onChange={(x) => setE({ ...e, confidence: x.target.value as KnowledgeEntry["confidence"] })}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label>
          <label className="row" style={{ gap: 6, marginTop: 18 }}><input type="checkbox" checked={testOn} onChange={(x) => setTestOn(x.target.checked)} /> Check it against the runs</label>
        </div>
        {testOn && (
          <div className="belief-test col" style={{ gap: 10 }}>
            <div className="row wrap" style={{ gap: 12 }}>
              <label className="field">Kind of claim<select className="input" value={t.type} onChange={(x) => setT({ type: x.target.value as BeliefTest["type"] })}>
                <option value="config_effect">changing a setting moves a metric</option><option value="loss_behaviour">a loss behaves a certain way</option></select></label>
              <label className="field" style={{ flex: 1, minWidth: 260 }}>Which runs (search, like the Runs page)<input className="input" value={t.scope ?? ""} onChange={(x) => setT({ scope: x.target.value })} placeholder="empty = newest runs · e.g. experiment:sparse_detection" /></label>
            </div>
            {t.type === "config_effect" ? (
              <div className="row wrap" style={{ gap: 10, alignItems: "flex-end" }}>
                <label className="field">When I<select className="input" value={t.change} onChange={(x) => setT({ change: x.target.value as BeliefTest["change"] })}><option value="increase">increase</option><option value="decrease">decrease</option><option value="set">set</option></select></label>
                <label className="field" style={{ minWidth: 240 }}>setting<input className="input mono" list="belief-keys" value={t.key ?? ""} onChange={(x) => setT({ key: x.target.value })} placeholder="optimizer.lr" /></label>
                {t.change === "set" && <label className="field">to<input className="input mono" value={t.value ?? ""} onChange={(x) => setT({ value: x.target.value })} placeholder="True" /></label>}
                <label className="field" style={{ minWidth: 260 }}>the metric<input className="input mono" list="belief-metrics" value={t.metric ?? ""} onChange={(x) => setT({ metric: x.target.value })} placeholder="val/mAP" /></label>
                <label className="field">goes<select className="input" value={t.expect} onChange={(x) => setT({ expect: x.target.value as BeliefTest["expect"] })}><option value="up">up</option><option value="down">down</option><option value="no_change">nowhere</option></select></label>
                <label className="field" title="A relative change smaller than this (or than the seed noise) counts as no effect">effect ≥<input className="input" type="number" step="0.5" min="0" max="100" style={{ width: 80 }} value={Math.round((t.min_effect ?? 0.01) * 1000) / 10} onChange={(x) => setT({ min_effect: Number(x.target.value) / 100 })} /> %</label>
                <datalist id="belief-keys">{keys.map((k) => <option key={k.key} value={k.key} />)}</datalist>
                <datalist id="belief-metrics">{(vocab.data?.metrics ?? []).map((m) => <option key={m} value={m} />)}</datalist>
              </div>
            ) : (
              <div className="row wrap" style={{ gap: 10, alignItems: "flex-end" }}>
                <label className="field">Loss family<input className="input mono" list="belief-families" value={t.family ?? ""} onChange={(x) => setT({ family: x.target.value })} placeholder="radar" /></label>
                <label className="field">is<select className="input" value={t.behaviour} onChange={(x) => setT({ behaviour: x.target.value as BeliefTest["behaviour"] })}>
                  <option value="muted">muted by σ</option><option value="trusted">trusted by σ</option><option value="plateaus_early">done learning early</option><option value="still_learning">still learning at the end</option><option value="rising">rising</option></select></label>
                <datalist id="belief-families">{(vocab.data?.families ?? []).map((f) => <option key={f} value={f} />)}</datalist>
              </div>
            )}
            {vocab.isFetching && <span className="faint" style={{ fontSize: 12 }}>reading the runs in scope for suggestions…</span>}
          </div>
        )}
        {save.error && <ErrorBox error={save.error} />}
        <div className="row" style={{ gap: 8 }}>
          <button className="btn ghost" onClick={onClose}>Cancel</button><span className="spacer" />
          <button className="btn primary" disabled={!ok || save.isPending} onClick={() => save.mutate()}>{testOn ? "Save and check" : "Save"}</button>
        </div>
      </div>
    </Card>
  );
}
