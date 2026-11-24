import { useQueries, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Layers, Plus, X } from "lucide-react";
import { useState } from "react";
import { api, refStr } from "../api/client";
import { RunPicker } from "./RunPicker";
import { StatusPill } from "./ui";

/** The runs of a subject: ``group:<run>+<run>`` (several runs — or pipeline stages — that together hold one model's
 *  results) or one run. */
export const membersOf = (v: string | null | undefined): string[] => (!v ? [] : v.startsWith("group:") ? v.slice(6).split("+").filter(Boolean) : [v]);
export const toSubject = (runs: string[]): string => {
  const u = [...new Set(runs)];
  return u.length > 1 || (u[0] ?? "").includes("!") ? `group:${u.join("+")}` : u[0] ?? "";
};
/** ``ws:pipe!a!b`` — the pipeline ``ws:pipe`` without its stages a and b (they stay listed, unticked). */
export const splitMember = (m: string): { base: string; without: string[] } => {
  const [base, ...without] = m.split("!");
  return { base: base ?? m, without: without.filter(Boolean) };
};
const withoutMember = (base: string, without: string[]): string => (without.length ? [base, ...without].join("!") : base);

/** A baseline or candidate: one run, several run ids whose results belong together (+ run), or **chosen stages** —
 *  untick a failed stage of a pipeline, or take the training stage of one run and the evaluation stage of another
 *  (+ stage). Everything else treats it as one subject. */
export function SubjectPicker({ value, onChange, placeholder = "Select run…", width = 320, label }: {
  value: string | null; onChange: (v: string) => void; placeholder?: string; width?: number; label?: string;
}) {
  const members = membersOf(value);
  const [adding, setAdding] = useState<"run" | "stage" | null>(null);
  const [stageSource, setStageSource] = useState<string | null>(null);
  const set = (list: string[]): void => onChange(toSubject(list));
  // stages of one pipeline stay together as that pipeline (its name, which stages are in): untick more stages there
  // a pipeline without some stages is one member (``pipe!stage``) — it stays that pipeline whatever the index knows;
  // stage runs listed one by one (older links) are grouped under their pipeline when the index knows it
  const lights = useQueries({ queries: members.map((m) => ({ queryKey: ["run-light", m], queryFn: () => api.runLight(m), staleTime: 60_000, enabled: !m.includes("!") })) });
  const parentOf = (i: number): string | null => { const r = lights[i]?.data?.run; return r?.parent ? refStr(r.parent) : null; };
  const groups: { key: string; pipeline: string | null; refs: string[]; without?: string[] }[] = [];
  members.forEach((m, i) => {
    if (m.includes("!")) { const { base, without } = splitMember(m); groups.push({ key: m, pipeline: base, refs: [m], without }); return; }
    const par = parentOf(i);
    const g = par ? groups.find((x) => x.pipeline === par && !x.without) : undefined;
    if (g) g.refs.push(m); else groups.push({ key: par ?? m, pipeline: par, refs: [m] });
  });
  const replace = (old: string[], next: string[]): void => {
    const out: string[] = [];
    let done = false;
    for (const m of members) {
      if (!old.includes(m)) { out.push(m); continue; }
      if (!done) { out.push(...next); done = true; }
    }
    set(out);
  };
  if (members.length === 0) return <RunPicker value={null} onChange={onChange} placeholder={placeholder} width={width} />;
  return (
    <div className="subject" style={{ width }} data-testid="subject-picker" aria-label={label}>
      <div className="col" style={{ gap: 4 }}>
        {groups.map((g) => g.pipeline
          ? <PipelineStages key={g.key} pipeline={g.pipeline} chosen={g.without ? null : g.refs} without={g.without ?? null}
              onRemove={() => replace(g.refs, [])} onApply={(left) => replace(g.refs, [withoutMember(g.pipeline!, left)])} />
          : <Member key={g.key} id={g.refs[0]!}
              onRemove={() => replace(g.refs, [])}
              onStages={(left) => replace(g.refs, [withoutMember(g.refs[0]!, left)])} />)}
      </div>
      <div className="row" style={{ gap: 4, marginTop: 2 }}>
        {adding === null && <>
          <button type="button" className="btn ghost sm" title="Add another run id that holds results of the same model (e.g. its evaluation job)" onClick={() => setAdding("run")}><Plus size={12} /> run</button>
          <button type="button" className="btn ghost sm" title="Add one stage of another run (e.g. its evaluation stage)" onClick={() => { setAdding("stage"); setStageSource(null); }}><Layers size={12} /> stage</button>
        </>}
        {adding && <button type="button" className="btn ghost sm" onClick={() => setAdding(null)}>cancel</button>}
      </div>
      {members.length > 1 && <div className="faint" style={{ fontSize: 11.5 }}>{members.length} runs / stages, compared as one</div>}
      {adding === "run" && <div style={{ marginTop: 4 }}><RunPicker value={null} placeholder="Add a run with results of the same model…" width={width - 12} onChange={(v) => { set([...members, v]); setAdding(null); }} /></div>}
      {adding === "stage" && (
        <div style={{ marginTop: 4 }}>
          {!stageSource
            ? <RunPicker value={null} placeholder="Pick the pipeline run with that stage…" width={width - 12} onChange={setStageSource} />
            : <StageList run={stageSource} chosen={members} onToggle={(ref, on) => { set(on ? [...members, ref] : members.filter((x) => x !== ref)); }} />}
        </div>
      )}
    </div>
  );
}

/** One member: its name and status; a pipeline can be opened to choose its stages (failed ones are flagged). */
function Member({ id, onRemove, onStages }: { id: string; onRemove: () => void; onStages: (leftOut: string[]) => void }) {
  const light = useQuery({ queryKey: ["run-light", id], queryFn: () => api.runLight(id), staleTime: 60_000 });
  const [open, setOpen] = useState(false);
  const run = light.data?.run;
  const parent = run?.parent ? refStr(run.parent) : null;
  const parentLight = useQuery({ queryKey: ["run-light", parent], queryFn: () => api.runLight(parent!), enabled: !!parent, staleTime: 60_000 });
  const isPipe = !!run && run.children.length > 0;
  const name = run ? `${parentLight.data ? `${parentLight.data.run.display_name ?? parentLight.data.run.name} / ` : ""}${run.display_name ?? run.name}` : id;
  return (
    <div className="member">
      <div className="row" style={{ gap: 4, minWidth: 0 }}>
        {isPipe
          ? <button type="button" className="icon-btn" aria-label={`Stages of ${name}`} title="Choose this pipeline's stages" onClick={() => setOpen(!open)}>{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>
          : <span style={{ width: 20 }} />}
        {run && <StatusPill status={run.status} />}
        <span className="trunc" style={{ flex: 1, fontSize: 12.5 }} title={`${name}\n${id}`}>{name}</span>
        <X size={11} style={{ cursor: "pointer", flex: "none" }} aria-label={`Remove ${id}`} onClick={onRemove} />
      </div>
      {open && isPipe && <StageList run={id} without={[]} onLeftOut={onStages} />}
    </div>
  );
}

/** One pipeline with some stages left out: shown as that pipeline ("3 of 4 stages · without publish"), its stage list
 *  kept with the left-out ones unticked — tick one back any time; all ticked gives the whole pipeline back.
 *  ``without``: the stages left out (``pipe!stage``); ``chosen``: stage runs listed one by one (older links). */
function PipelineStages({ pipeline, chosen, without, onRemove, onApply }: {
  pipeline: string; chosen: string[] | null; without: string[] | null; onRemove: () => void; onApply: (leftOut: string[]) => void;
}) {
  const light = useQuery({ queryKey: ["run-light", pipeline], queryFn: () => api.runLight(pipeline), staleTime: 60_000 });
  const st = useQuery({ queryKey: ["stages", pipeline], queryFn: () => api.stages(pipeline), staleTime: 60_000 });
  const [open, setOpen] = useState(true);
  const run = light.data?.run;
  const name = run ? run.display_name ?? run.name : pipeline;
  const all = st.data ?? [];
  const out = without ?? all.filter((s) => !(chosen ?? []).includes(s.ref)).map((s) => s.name);
  const kept = all.length ? all.length - out.length : (chosen ?? []).length;
  return (
    <div className="member" data-testid="pipeline-member">
      <div className="row" style={{ gap: 4, minWidth: 0 }}>
        <button type="button" className="icon-btn" aria-label={`Stages of ${name}`} title="Choose this pipeline's stages" onClick={() => setOpen(!open)}>{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>
        {run && <StatusPill status={run.status} />}
        <span className="trunc" style={{ flex: 1, fontSize: 12.5 }} title={`${name}\n${pipeline}`}>{name}</span>
        <X size={11} style={{ cursor: "pointer", flex: "none" }} aria-label={`Remove ${pipeline}`} onClick={onRemove} />
      </div>
      <div className="faint" style={{ fontSize: 11, paddingLeft: 24 }} data-testid="pipeline-stages-summary">
        {all.length ? `${kept} of ${all.length} stages${out.length ? ` · without ${out.join(", ")}` : ""}` : `without ${out.join(", ")}`}
      </div>
      {open && <StageList run={pipeline} without={out} onLeftOut={onApply} />}
    </div>
  );
}

/** A pipeline's stages with their status. ``without`` / ``onLeftOut``: the pipeline is in the subject — unticking a
 *  stage leaves it out (it stays listed, unticked, to tick back later); at least one stage stays in. ``chosen`` /
 *  ``onToggle``: adding single stages of another run. */
function StageList({ run, chosen = [], without, onLeftOut, onToggle }: {
  run: string; chosen?: string[]; without?: string[]; onLeftOut?: (leftOut: string[]) => void; onToggle?: (ref: string, on: boolean) => void;
}) {
  const whole = without !== undefined;
  const st = useQuery({ queryKey: ["stages", run], queryFn: () => api.stages(run) });
  if (!st.data) return <div className="faint" style={{ fontSize: 12, padding: 4 }}>loading stages…</div>;
  if (!st.data.length) return <div className="faint" style={{ fontSize: 12, padding: 4 }}>this run has no stages</div>;
  return (
    <div className="stage-list" data-testid="stage-list">
      {st.data.map((s) => {
        const on = whole ? !without!.includes(s.name) : chosen.includes(s.ref);
        const last = whole && on && st.data!.filter((x) => !without!.includes(x.name)).length === 1;
        return (
          <label key={s.ref} className={`row ${s.status === "failed" ? "stage-failed" : ""}`} style={{ gap: 6 }} title={s.failure ?? s.ref}>
            <input type="checkbox" checked={on} disabled={last} aria-label={`stage ${s.name}`} title={last ? "at least one stage stays in" : undefined}
              onChange={() => {
                if (!whole) { onToggle?.(s.ref, !on); return; }
                onLeftOut?.(on ? [...without!, s.name] : without!.filter((n) => n !== s.name));
              }} />
            <span className={`mono trunc${whole && !on ? " faint stage-out" : ""}`} style={{ flex: 1 }}>{s.name}</span>
            {s.status !== "unknown" && <StatusPill status={s.status as never} />}
            {s.status === "failed" && <span className="bad" style={{ fontSize: 11 }}>untick to exclude</span>}
          </label>
        );
      })}
    </div>
  );
}

/** The name of a subject as people know it: a pipeline with some stages ticked keeps the pipeline's name (and says
 *  which stages are in); other groups list their runs. */
export function SubjectLabel({ id, fallback }: { id: string; fallback: string }) {
  const members = membersOf(id);
  const single = members.length === 1 && members[0]!.includes("!") ? splitMember(members[0]!) : null;
  const sl = useQuery({ queryKey: ["run-light", single?.base], queryFn: () => api.runLight(single!.base), enabled: !!single, staleTime: 60_000 });
  const sst = useQuery({ queryKey: ["stages", single?.base], queryFn: () => api.stages(single!.base), enabled: !!single, staleTime: 60_000 });
  const lights = useQueries({ queries: members.map((m) => ({ queryKey: ["run-light", m], queryFn: () => api.runLight(m), staleTime: 60_000, enabled: !m.includes("!") })) });
  const parents = [...new Set(lights.map((l) => (l.data?.run.parent ? refStr(l.data.run.parent) : "")))];
  const pipeline = members.length > 1 && parents.length === 1 && parents[0] ? parents[0] : null;
  const pl = useQuery({ queryKey: ["run-light", pipeline], queryFn: () => api.runLight(pipeline!), enabled: !!pipeline, staleTime: 60_000 });
  const st = useQuery({ queryKey: ["stages", pipeline], queryFn: () => api.stages(pipeline!), enabled: !!pipeline, staleTime: 60_000 });
  if (single) {
    return (
      <span className="col" style={{ gap: 1 }} data-testid="subject-label">
        <span>{sl.data ? sl.data.run.display_name ?? sl.data.run.name : single.base}</span>
        <span className="faint" style={{ fontSize: 11, fontWeight: 400 }}>{sst.data?.length ? `${sst.data.length - single.without.length} of ${sst.data.length} stages · ` : ""}without {single.without.join(", ")}</span>
      </span>
    );
  }
  if (members.length < 2) return <>{fallback}</>;
  if (pipeline && pl.data) {
    const all = st.data ?? [];
    const out = all.filter((s) => !members.includes(s.ref)).map((s) => s.name);
    return (
      <span className="col" style={{ gap: 1 }} data-testid="subject-label">
        <span>{pl.data.run.display_name ?? pl.data.run.name}</span>
        <span className="faint" style={{ fontSize: 11, fontWeight: 400 }}>{all.length ? `${members.length} of ${all.length} stages` : `${members.length} stages`}{out.length ? ` · without ${out.join(", ")}` : ""}</span>
      </span>
    );
  }
  const names = lights.map((l, i) => l.data?.run.display_name ?? l.data?.run.name ?? members[i]!.split(":").pop());
  return <span data-testid="subject-label">{names.join(" + ")}</span>;
}
