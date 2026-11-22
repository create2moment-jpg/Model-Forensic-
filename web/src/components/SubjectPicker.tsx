import { useQuery } from "@tanstack/react-query";
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
  return u.length > 1 ? `group:${u.join("+")}` : u[0] ?? "";
};

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
  if (members.length === 0) return <RunPicker value={null} onChange={onChange} placeholder={placeholder} width={width} />;
  return (
    <div className="subject" style={{ width }} data-testid="subject-picker" aria-label={label}>
      <div className="col" style={{ gap: 4 }}>
        {members.map((m) => (
          <Member key={m} id={m}
            onRemove={() => set(members.filter((x) => x !== m))}
            onStages={(stages) => set(members.flatMap((x) => (x === m ? stages : [x])))} />
        ))}
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
function Member({ id, onRemove, onStages }: { id: string; onRemove: () => void; onStages: (stages: string[]) => void }) {
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
      {open && isPipe && <StageList run={id} chosen={run!.children.map(refStr)} whole onApply={onStages} />}
    </div>
  );
}

/** A pipeline's stages with their status. ``whole``: the pipeline is in the subject — unticking a stage replaces the
 *  pipeline by its other stages. Otherwise each tick adds or removes that stage. */
function StageList({ run, chosen, whole = false, onApply, onToggle }: {
  run: string; chosen: string[]; whole?: boolean; onApply?: (stages: string[]) => void; onToggle?: (ref: string, on: boolean) => void;
}) {
  const st = useQuery({ queryKey: ["stages", run], queryFn: () => api.stages(run) });
  if (!st.data) return <div className="faint" style={{ fontSize: 12, padding: 4 }}>loading stages…</div>;
  if (!st.data.length) return <div className="faint" style={{ fontSize: 12, padding: 4 }}>this run has no stages</div>;
  return (
    <div className="stage-list" data-testid="stage-list">
      {st.data.map((s) => {
        const on = chosen.includes(s.ref);
        return (
          <label key={s.ref} className={`row ${s.status === "failed" ? "stage-failed" : ""}`} style={{ gap: 6 }} title={s.failure ?? s.ref}>
            <input type="checkbox" checked={on} aria-label={`stage ${s.name}`}
              onChange={() => (whole ? onApply?.(st.data!.filter((x) => (x.ref === s.ref ? !on : chosen.includes(x.ref))).map((x) => x.ref)) : onToggle?.(s.ref, !on))} />
            <span className="mono trunc" style={{ flex: 1 }}>{s.name}</span>
            {s.status !== "unknown" && <StatusPill status={s.status as never} />}
            {s.status === "failed" && <span className="bad" style={{ fontSize: 11 }}>untick to exclude</span>}
          </label>
        );
      })}
    </div>
  );
}
