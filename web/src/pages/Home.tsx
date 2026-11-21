import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, ArrowRight, Bot, CheckCircle2, Clock, GitCompare, RefreshCw, Search, Sparkles } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api, refStr } from "../api/client";
import type { Run } from "../api/types";
import { useApp } from "../components/AppState";
import { Card, Empty, Skeleton, StatusPill } from "../components/ui";
import { fmtAgo, fmtNum } from "../lib/format";

export function HomePage() {
  const nav = useNavigate();
  const { trackJob } = useApp();
  const all = useQuery({ queryKey: ["runs", "home-all"], queryFn: () => api.search("", 500) });
  const failed = useQuery({ queryKey: ["runs", "home-failed"], queryFn: () => api.search("status:failed", 6) });
  const invs = useQuery({ queryKey: ["investigations"], queryFn: () => api.investigations() });
  const runs = all.data?.runs ?? [];
  const count = (s: string): number => runs.filter((r) => r.status === s).length;
  const week = runs.filter((r) => r.created_at && Date.now() - new Date(r.created_at).getTime() < 7 * 864e5).length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Command center</h1>
          <div className="sub">From “something is wrong” to evidence, hypotheses and the next experiment.</div>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => void api.sync().then((r) => trackJob(r.job_id))}><RefreshCw size={14} /> Sync</button>
          <button className="btn primary" onClick={() => nav("/compare")}><GitCompare size={14} /> Compare runs</button>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat label="Indexed runs" icon={<Search size={13} />} value={all.data?.total} />
        <Stat label="Created this week" icon={<Clock size={13} />} value={all.isLoading ? undefined : week} />
        <Stat label="Completed" icon={<CheckCircle2 size={13} color="var(--good)" />} value={all.isLoading ? undefined : count("completed")} />
        <Stat label="Failed" icon={<AlertOctagon size={13} color="var(--bad)" />} value={all.isLoading ? undefined : count("failed")} tone={count("failed") ? "var(--bad)" : undefined} />
      </div>

      <div className="split">
        <Card title="Recent runs" right={<Link to="/runs" className="btn ghost sm">All runs <ArrowRight size={13} /></Link>} flush>
          {all.isLoading ? <div className="card-body col">{[1, 2, 3, 4, 5].map((i) => <Skeleton key={i} h={22} />)}</div> : runs.length === 0 ? (
            <Empty title="No runs indexed yet" action={<button className="btn primary" onClick={() => void api.sync().then((r) => trackJob(r.job_id))}><RefreshCw size={14} /> Sync backends</button>}>
              Configure a backend in <code>model-forensic.yaml</code>, or run <code>mf demo</code> for synthetic runs.
            </Empty>
          ) : <RunMiniTable runs={runs.slice(0, 12)} />}
        </Card>
        <div className="col" style={{ gap: 14 }}>
          <Card title="Needs attention" sub="failed runs" flush>
            {(failed.data?.runs ?? []).length === 0 ? <Empty icon={<CheckCircle2 size={24} color="var(--good)" />} title="No failures" /> : (
              <div>
                {failed.data!.runs.map((r) => (
                  <Link key={refStr(r.ref)} to={`/runs/${encodeURIComponent(refStr(r.ref))}`} className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)" }}>
                    <AlertOctagon size={15} color="var(--bad)" />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="ellipsis" style={{ fontWeight: 500 }}>{r.name}</div>
                      <div className="faint ellipsis" style={{ fontSize: 12 }}>{r.failure?.message ?? "failed"}</div>
                    </div>
                    <span className="faint" style={{ fontSize: 12 }}>{fmtAgo(r.created_at)}</span>
                  </Link>
                ))}
              </div>
            )}
          </Card>
          <Card title="Recent AI investigations" right={<Link to="/ai" className="btn ghost sm"><Bot size={13} /> Ask</Link>} flush>
            {(invs.data ?? []).length === 0 ? <Empty icon={<Sparkles size={22} />} title="No investigations yet">Open a run and ask “why is this worse than baseline?”</Empty> : (
              invs.data!.slice(0, 5).map((i) => (
                <Link key={i.id} to={`/ai?inv=${i.id}`} style={{ display: "block", padding: "10px 16px", borderBottom: "1px solid var(--line)" }}>
                  <div className="ellipsis" style={{ fontWeight: 500 }}>{i.question}</div>
                  <div className="row faint" style={{ fontSize: 12 }}><span>{Math.round(i.verified_ratio * 100)}% verified</span><span>·</span><span className="ellipsis">{i.model}</span><span className="spacer" />{fmtAgo(i.created_at)}</div>
                </Link>
              ))
            )}
          </Card>
          <Card title="Shortcuts">
            <div className="col" style={{ gap: 6 }}>
              {[["⌘K", "Command palette / search"], ["g r", "Runs"], ["g c", "Compare"], ["g a", "AI investigation"], ["g s", "Settings"]].map(([k, l]) => (
                <div key={k} className="row"><span className="kbd">{k}</span><span className="muted">{l}</span></div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, icon, tone }: { label: string; value: number | undefined; icon: React.ReactNode; tone?: string }) {
  return (
    <div className="card stat">
      <div className="label">{icon}{label}</div>
      <div className="value" style={{ color: tone }}>{value === undefined ? <Skeleton h={28} w={60} /> : value}</div>
    </div>
  );
}

function RunMiniTable({ runs }: { runs: Run[] }) {
  const nav = useNavigate();
  return (
    <div className="table-wrap">
      <table className="t">
        <thead><tr><th>Run</th><th>Status</th><th>Experiment</th><th>User</th><th className="r">NDS</th><th className="r">Created</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={refStr(r.ref)} className="clickable" onClick={() => nav(`/runs/${encodeURIComponent(refStr(r.ref))}`)}>
              <td><div style={{ fontWeight: 500 }}>{r.name}</div>{r.description && <div className="faint ellipsis" style={{ fontSize: 12, maxWidth: 340 }}>{r.description}</div>}</td>
              <td><StatusPill status={r.status} /></td>
              <td className="muted">{r.experiment ?? "—"}</td>
              <td className="muted">{r.user ?? "—"}</td>
              <td className="r num">{fmtNum(r.summary_metrics.NDS)}</td>
              <td className="r faint">{fmtAgo(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
