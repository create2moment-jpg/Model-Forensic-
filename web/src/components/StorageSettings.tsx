import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, HardDrive, RotateCcw, Zap } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { StorageRole } from "../api/types";
import { useApp } from "./AppState";
import { Card, ErrorBox, Loading, Pill } from "./ui";
import { fmtBytes } from "../lib/format";

/** Settings → Storage & performance: where each kind of data lives (validated), what is safe to delete, and how many
 *  parallel downloads to run. Paths apply at the next start; the worker count applies immediately. */
export function StorageSettings() {
  const qc = useQueryClient();
  const { toast } = useApp();
  const q = useQuery({ queryKey: ["storage"], queryFn: api.storage });
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [checks, setChecks] = useState<Record<string, StorageRole>>({});
  const [restart, setRestart] = useState(false);
  const save = useMutation({
    mutationFn: (body: Record<string, string | number | null>) => api.saveStorage(body),
    onSuccess: (r) => { qc.setQueryData(["storage"], r); setEdit({}); setChecks({}); if (r.restart_required) setRestart(true); toast(r.restart_required ? "Saved — restart Model Forensic to use the new paths" : "Saved", "good"); },
  });
  if (q.isLoading) return <Card title="Storage & performance"><Loading /></Card>;
  if (q.error || !q.data) return <Card title="Storage & performance"><ErrorBox error={q.error} /></Card>;
  const d = q.data;
  const w = d.workers;
  const validate = (role: string, path: string): void => { if (path.trim()) void api.validateStorage(role, path).then((r) => setChecks((c) => ({ ...c, [role]: r }))); };
  const pending = Object.keys(edit).length > 0;
  const blocked = Object.entries(edit).some(([role, v]) => v.trim() && checks[role] && !checks[role]!.ok);
  return (
    <Card title={<span className="row"><HardDrive size={15} /> Storage &amp; performance</span>} sub={`Data folder ${d.data_dir} — put fast roles on an SSD and downloaded files on a large disk if they differ`}>
      <div className="col" style={{ gap: 12 }}>
        {restart && <div className="note warn"><RotateCcw size={14} /> Restart Model Forensic to move to the new paths (nothing is copied — move existing files yourself or let caches refill).</div>}
        <table className="tbl storage-tbl">
          <thead><tr><th>Role</th><th>Location</th><th className="r">Used</th><th className="r">Free</th><th>Disk</th><th>Deleting it</th></tr></thead>
          <tbody>
            {d.roles.map((r) => {
              const chk = checks[r.role];
              const val = edit[r.role] ?? "";
              return (
                <tr key={r.role}>
                  <td><b>{r.title}</b><div className="faint" style={{ fontSize: 11 }}>{r.speed} · {r.role}</div></td>
                  <td style={{ minWidth: 280 }}>
                    <input className="input mono" style={{ width: "100%", fontSize: 12 }} aria-label={`${r.role} path`} placeholder={r.path} value={val}
                      onChange={(e) => setEdit({ ...edit, [r.role]: e.target.value })} onBlur={() => validate(r.role, val)} />
                    {!r.ok && !val && r.problems.map((p) => <div key={p} className="bad" style={{ fontSize: 11 }}>{p}</div>)}
                    {chk && val && (chk.ok ? <div className="good-text" style={{ fontSize: 11 }}>✓ usable · {fmtBytes(chk.free_bytes)} free · {chk.device}{chk.notes.length ? ` · ${chk.notes.join("; ")}` : ""}</div>
                      : chk.problems.map((p) => <div key={p} className="bad" style={{ fontSize: 11 }}>{p}</div>))}
                    {!val && r.notes.map((n) => <div key={n} className="faint" style={{ fontSize: 11 }}>{n}</div>)}
                  </td>
                  <td className="r num">{fmtBytes(r.used_bytes)}</td>
                  <td className="r num">{fmtBytes(r.free_bytes)}</td>
                  <td><Pill tone={r.device === "ssd" ? "good" : r.device === "hdd" ? "outline" : "outline"}>{r.device}</Pill></td>
                  <td className="faint" style={{ fontSize: 12, maxWidth: 320 }}>{r.delete}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="row" style={{ gap: 8 }}>
          <span className="faint" style={{ fontSize: 12 }}>Leave a field empty to keep the current location. Saved to <code>config/storage.local.yaml</code> (git-ignored).</span>
          <span className="spacer" />
          <button className="btn sm ghost" disabled={!pending} onClick={() => { setEdit({}); setChecks({}); }}>Reset</button>
          <button className="btn sm primary" disabled={!pending || blocked || save.isPending}
            onClick={() => save.mutate(Object.fromEntries(Object.entries(edit).filter(([, v]) => v.trim()).map(([k, v]) => [`${k}_dir`, v.trim()])))}>Save paths</button>
        </div>
        {save.error && <ErrorBox error={save.error} />}
        <details>
          <summary className="faint" style={{ cursor: "pointer", fontSize: 13 }}>Which files are safe to delete</summary>
          <table className="tbl" style={{ marginTop: 6 }}><tbody>{d.files.map((f) => (
            <tr key={f.path}><td className="mono" style={{ fontSize: 12 }}>{f.path}</td><td><Pill tone={f.kind === "required" ? "bad" : f.kind === "resumable" ? "warn" : "outline"}>{f.kind}</Pill></td><td className="faint" style={{ fontSize: 12 }}>{f.what}</td></tr>
          ))}</tbody></table>
        </details>
        <div className="workers-box">
          <div className="row" style={{ gap: 8 }}><Cpu size={15} /><b>Parallel downloads</b><span className="faint" style={{ fontSize: 12 }}>{w.why}</span></div>
          <div className="row wrap" style={{ gap: 8, marginTop: 8 }}>
            <span>now <b>{w.current}</b></span>
            <button className={`btn sm ${w.current === w.recommended ? "primary" : ""}`} onClick={() => save.mutate({ download_workers: w.recommended })}>Recommended · {w.recommended}</button>
            <button className={`btn sm ${w.current === w.boost ? "primary" : ""}`} title={w.boost_implications} onClick={() => { if (confirm(`Boost to ${w.boost} parallel downloads?\n\n${w.boost_implications}`)) save.mutate({ download_workers: w.boost }); }}><Zap size={13} /> Boost · {w.boost}</button>
            <label className="row" style={{ gap: 6 }}>custom
              <input className="input" type="number" min={1} max={64} style={{ width: 70 }} aria-label="download workers" defaultValue={w.current}
                onKeyDown={(e) => { if (e.key === "Enter") save.mutate({ download_workers: Number((e.target as HTMLInputElement).value) }); }} /></label>
          </div>
          <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>Applies to the next downloads immediately. Boost: {w.boost_implications}.</div>
        </div>
      </div>
    </Card>
  );
}
