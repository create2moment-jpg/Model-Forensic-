import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, PlugZap, Wrench, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConnCheck, Diagnosis } from "../api/types";

/** Cause + ordered fixes for a failed connection step (from the backend's diagnose rules). */
export function DiagnosisBox({ d }: { d: Diagnosis }) {
  return (
    <div className="diag">
      <div className="row" style={{ gap: 6 }}><Wrench size={14} /><b>{d.summary}</b><span className="faint mono">({d.cause.replace(/_/g, " ")})</span></div>
      <ol>{d.fixes.map((f) => <li key={f} className="mono">{f}</li>)}</ol>
    </div>
  );
}

/** Live connection test of one workspace: token → listing → run → metrics → naming → files, like `mf doctor --live`. */
export function ConnectionTest({ name, diagnosis }: { name: string; diagnosis?: Diagnosis | null }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [startErr, setStartErr] = useState<string | null>(null);
  const job = useQuery({
    queryKey: ["job", jobId], queryFn: () => api.job(jobId!), enabled: !!jobId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 700 : false),
  });
  const start = () => {
    setStartErr(null);
    api.checkBackend(name).then((r) => setJobId(r.job_id)).catch((e: Error) => setStartErr(e.message));
  };
  const qc = useQueryClient();
  const finished = job.data && job.data.status !== "running";
  useEffect(() => {
    if (finished) { void qc.invalidateQueries({ queryKey: ["backends"] }); void qc.invalidateQueries({ queryKey: ["health-deep"] }); }
  }, [finished, qc]);
  const running = !!jobId && (!job.data || job.data.status === "running");
  const res = job.data?.result as { ok: boolean; checks: ConnCheck[] } | undefined;
  return (
    <div className="conn-test">
      {!res && diagnosis && <DiagnosisBox d={diagnosis} />}
      <button className="btn sm" onClick={start} disabled={running}>
        {running ? <Loader2 size={13} className="spin" /> : <PlugZap size={13} />} {running ? "Testing…" : "Test connection"}
      </button>
      {startErr && <span className="faint">{startErr}</span>}
      {job.data?.status === "failed" && <span className="faint mono">{job.data.error}</span>}
      {res && (
        <table className="t compact" style={{ marginTop: 8 }}>
          <tbody>
            {res.checks.map((c, i) => (
              <tr key={i}>
                <td style={{ width: 22 }}>{c.ok ? <CheckCircle2 size={14} color={c.slow ? "var(--warn)" : "var(--good)"} /> : <XCircle size={14} color="var(--bad)" />}</td>
                <td className="mono">{c.name}</td>
                <td className="r num faint">{c.seconds != null ? `${c.seconds.toFixed(2)} s` : ""}</td>
                <td className="conn-detail">{c.detail}{c.slow && <span className="faint"> — slower than budget</span>}{c.diagnosis && <DiagnosisBox d={c.diagnosis} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
