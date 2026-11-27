import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, ArrowRight, History, X, Eye, EyeOff, Star, BookMarked, Bot, CheckCircle2, FileClock, GitCompare, Microscope, RefreshCw, RotateCcw, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, refStr } from "../api/client";
import type { Run } from "../api/types";
import { useApp } from "../components/AppState";
import { VerdictCard } from "../components/Verdict";
import { emoji } from "../lib/appearance";
import { useWatched } from "../lib/watch";
import { forget, useRecent } from "../lib/recent";
import { Card, Empty, Skeleton, StatusPill } from "../components/ui";
import { fmtAgo, fmtDuration, fmtNum } from "../lib/format";
import { SHORTCUTS } from "../lib/shortcuts";

export function HomePage() {
  const nav = useNavigate();
  const { trackJob, workspace } = useApp();
  const all = useQuery({ queryKey: ["runs", "home-all", workspace], queryFn: () => api.search("", 500, 0, workspace, true) });  // pipelines and runs; stages are under their pipeline
  const failed = useQuery({ queryKey: ["runs", "home-failed", workspace], queryFn: () => api.search("status:failed", 6, 0, workspace, true) });
  const invs = useQuery({ queryKey: ["investigations"], queryFn: () => api.investigations() });
  const runs = all.data?.runs ?? [];
  const count = (s: string): number => runs.filter((r) => r.status === s).length;
  const week = runs.filter((r) => r.created_at && Date.now() - new Date(r.created_at).getTime() < 7 * 864e5).length;
  // your newest run, answered: from what is loaded; a local run is read at once, a remote one on one click
  const newest = runs.find((r) => !r.parent);
  const nid = newest ? refStr(newest.ref) : "";
  const [read, setRead] = useState(false);
  const vq = useQuery({ queryKey: ["verdict", nid, "home", read], enabled: !!nid, retry: false,
    queryFn: async () => {
      const v = await api.verdict(nid, null, null, !read);
      return v.state === "not_loaded" && !v.remote ? api.verdict(nid, null, null, false) : v;
    } });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Today</h1>
          <div className="greeting faint" data-testid="greeting">{greeting()}</div>
          <div className="sub" data-testid="home-counts">{all.isLoading ? "…" : `${all.data?.total ?? 0} runs · ${week} this week · ${count("running")} running · `}{!all.isLoading && <span style={{ color: count("failed") ? "var(--bad)" : undefined }}>{count("failed")} failed</span>}</div>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => void api.sync(workspace).then((r) => trackJob(r.job_id))}><RefreshCw size={14} /> Sync</button>
          <button className="btn primary" onClick={() => nav("/compare")}><GitCompare size={14} /> Compare runs</button>
        </div>
      </div>

      {newest && (
        <div data-testid="newest-run">
          <div className="row" style={{ gap: 8, margin: "2px 0 8px" }}>
            <span className="faint" style={{ fontSize: 12.5 }}>Your newest run</span>
            <Link to={`/runs/${encodeURIComponent(nid)}`} className="mono" style={{ fontSize: 13 }}>{newest.display_name ?? newest.name}</Link>
            <StatusPill status={newest.status} />
            <span className="faint" style={{ fontSize: 12.5 }}>{fmtAgo(newest.created_at)}</span>
            <span className="spacer" />
            <Link to="/check" className="btn ghost sm">Check another <ArrowRight size={13} /></Link>
          </div>
          <VerdictCard v={vq.data} loading={vq.isFetching && !!vq.data}
            onOpen={(ks) => nav(`/runs/${encodeURIComponent(nid)}?tab=metrics&mk=${encodeURIComponent(ks.join("|"))}`)}
            onNext={(n) => nav(`/runs/${encodeURIComponent(nid)}?tab=${n.view ?? (n.tab === "files" ? "logs" : n.tab === "compare" || n.tab === "summary" ? "overview" : n.tab ?? "review")}`)}
            onLoad={() => setRead(true)} />
        </div>
      )}

      <div className="split">
        <Card title="Recent runs" right={<Link to="/runs" className="btn ghost sm">All runs <ArrowRight size={13} /></Link>} flush>
          {all.isLoading ? <div className="card-body col">{[1, 2, 3, 4, 5].map((i) => <Skeleton key={i} h={22} />)}</div> : runs.length === 0 ? (
            <Empty title="No runs indexed yet" action={<button className="btn primary" onClick={() => void api.sync(workspace).then((r) => trackJob(r.job_id))}><RefreshCw size={14} /> Sync backends</button>}>
              Configure a backend in <code>model-forensic.yaml</code>, or run <code>mf demo</code> for synthetic runs.
            </Empty>
          ) : <RunMiniTable runs={runs.slice(0, 12)} />}
        </Card>
        <div className="col" style={{ gap: 14 }}>
          <Card title="Needs attention" sub="failed runs" flush>
            {(failed.data?.runs ?? []).length === 0 ? <Empty icon={<CheckCircle2 size={24} color="var(--good)" />} title={`${emoji("🎉")}No failures`} /> : (
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
          <Card title="Running now" flush>
            {runs.filter((r) => r.status === "running").length === 0 ? <div className="card-body muted">Nothing running.</div> : runs.filter((r) => r.status === "running").slice(0, 5).map((r) => (
              <Link key={refStr(r.ref)} to={`/runs/${encodeURIComponent(refStr(r.ref))}`} className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)" }}>
                <span className="pill accent running"><span className="dot" /></span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="ellipsis" style={{ fontWeight: 500 }}>{r.display_name ?? r.name}</div>
                  <div className="faint ellipsis" style={{ fontSize: 12 }}>{[r.user, r.experiment].filter(Boolean).join(" · ")}</div>
                </div>
                <span className="faint num" style={{ fontSize: 12 }} title="Running for">{r.started_at ? fmtDuration(r.started_at, new Date().toISOString()) : "starting"}</span>
              </Link>
            ))}
          </Card>
          <RecentCard />
          <WatchingCard />
          <StarredCard />
          <ContinueCard />
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
          <Card title="Shortcuts" right={<button className="btn ghost sm" onClick={() => window.dispatchEvent(new Event("mf-shortcuts"))}>All <span className="kbd">?</span></button>}>
            <div className="col" style={{ gap: 6 }}>
              {SHORTCUTS.filter((k) => k.where === "everywhere" && k.keys[0] !== "?").slice(0, 6).map((k) => (
                <div key={k.label} className="row"><span className="kbd">{k.keys.join(k.keys[0]!.length === 1 && k.keys.length === 2 && k.keys[1]!.length === 1 ? " " : " / ")}</span><span className="muted ellipsis">{k.label}</span></div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}


function RunMiniTable({ runs }: { runs: Run[] }) {
  const nav = useNavigate();
  const { workspace } = useApp();
  // the project's headline metric (ranked on the server, as the Runs page's columns) — never a hard-coded name
  const ids = runs.map((r) => refStr(r.ref));
  const mc = useQuery({ queryKey: ["metric-columns", ids], queryFn: () => api.metricColumns(ids), enabled: ids.length > 0, staleTime: 60_000 });
  // the default column most of these runs have a value for (a headline nobody logged is not worth a column)
  const filled = (m: string): number => runs.filter((r) => Number.isFinite(r.summary_metrics[m])).length;
  const metric = [...(mc.data?.default ?? [])].filter((m) => filled(m) > 0).sort((x, y) => filled(y) - filled(x))[0];
  return (
    <div className="table-wrap">
      <table className="t" data-testid="home-runs" style={{ tableLayout: "fixed", width: "100%" }}>
        <colgroup><col /><col style={{ width: 112 }} /><col style={{ width: "22%" }} />{metric && <col style={{ width: 96 }} />}<col style={{ width: 84 }} /></colgroup>
        <thead><tr><th>Run</th><th>Status</th><th>Experiment</th>{metric && <th className="r" title={metric}>{metric.length > 14 ? `${metric.slice(0, 13)}…` : metric}</th>}<th className="r">Created</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={refStr(r.ref)} className="clickable" onClick={() => nav(`/runs/${encodeURIComponent(refStr(r.ref))}`)}>
              <td style={{ overflow: "hidden" }}>
                <div className="row" style={{ fontWeight: 500, gap: 6, minWidth: 0 }}><span className="ellipsis">{r.display_name ?? r.name}</span>{r.children.length > 0 && <span className="faint" style={{ fontSize: 11.5, fontWeight: 400 }}>{r.children.length} stages</span>}</div>
                <div className="faint ellipsis" style={{ fontSize: 12 }}>{[!workspace ? r.ref.backend : null, r.user, r.description].filter(Boolean).join(" · ")}</div>
              </td>
              <td><StatusPill status={r.status} /></td>
              <td className="muted ellipsis" title={r.experiment ?? undefined}>{r.experiment ?? "—"}</td>
              {metric && <td className="r num">{fmtNum(r.summary_metrics[metric])}</td>}
              <td className="r faint">{fmtAgo(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Continue where you left off: drafts, open studies with their next step, jobs that did not finish, knowledge
 *  waiting for review — so half-finished work is one click away. */
function ContinueCard() {
  const { trackJob } = useApp();
  const w = useQuery({ queryKey: ["work"], queryFn: api.work });
  const d = w.data;
  const empty = d && d.drafts.length + d.studies.length + d.jobs.length === 0 && d.knowledge_proposed === 0;
  const row = { display: "block", padding: "9px 16px", borderBottom: "1px solid var(--line)" } as const;
  return (
    <Card title="Continue where you left off" right={<Link to="/studies" className="btn ghost sm">Studies <ArrowRight size={13} /></Link>} flush>
      {w.isLoading ? <div className="card-body"><Skeleton h={60} /></div> : empty ? <div className="card-body muted">Nothing half-finished. Start a <Link to="/study-wizard">guided study</Link>.</div> : (
        <div>
          {d!.drafts.map((x) => (
            <Link key={x.id} to={x.route} style={row}>
              <div className="row" style={{ gap: 6 }}><FileClock size={13} color="var(--warn)" /><span className="ellipsis" style={{ fontWeight: 500 }}>{x.title}</span><span className="spacer" /><span className="faint" style={{ fontSize: 12 }}>{fmtAgo(x.updated_at)}</span></div>
              <div className="faint" style={{ fontSize: 12 }}>draft · {x.kind === "study-wizard" ? "guided study" : x.kind.replace(/-/g, " ")}</div>
            </Link>
          ))}
          {d!.studies.slice(0, 5).map((st) => (
            <Link key={st.id} to={`/studies/${encodeURIComponent(st.id)}`} style={row}>
              <div className="row" style={{ gap: 6 }}><Microscope size={13} color="var(--accent-2)" /><span className="ellipsis" style={{ fontWeight: 500 }}>{st.title}</span><span className="spacer" /><span className="faint" style={{ fontSize: 12 }}>{fmtAgo(st.updated_at)}</span></div>
              <div className="faint" style={{ fontSize: 12 }}>next: {st.next}</div>
            </Link>
          ))}
          {d!.jobs.map((j) => (
            <div key={j.id} style={{ ...row, display: "flex", gap: 6, alignItems: "center" }}>
              <AlertOctagon size={13} color="var(--bad)" /><span className="ellipsis">{j.title || j.kind}</span><span className="faint" style={{ fontSize: 12 }}>{j.status}</span><span className="spacer" />
              {j.rerunnable && <button className="btn sm" onClick={() => void api.rerunJob(j.id).then((r) => trackJob(r.job_id))}><RotateCcw size={12} /> Run again</button>}
            </div>
          ))}
          {d!.knowledge_proposed > 0 && (
            <Link to="/knowledge" style={row}><div className="row" style={{ gap: 6 }}><BookMarked size={13} color="var(--accent)" /><span>{d!.knowledge_proposed} knowledge entr{d!.knowledge_proposed === 1 ? "y" : "ies"} waiting for review</span></div></Link>
          )}
          {d!.studies_open > 5 && <div className="faint" style={{ padding: "8px 16px", fontSize: 12 }}>{d!.studies_open - 5} more open studies</div>}
        </div>
      )}
    </Card>
  );
}

/** What you opened lately (runs and comparisons, in the view you left them) — per browser, from ``lib/recent``. */
function RecentCard() {
  const recent = useRecent();
  if (!recent.length) return null;
  return (
    <Card title={<span className="row"><History size={14} /> Recently opened</span>} right={<span className="faint" style={{ fontSize: 12 }}><span className="kbd">⌘K</span> too</span>} flush>
      <div data-testid="recent-card">
        {recent.slice(0, 5).map((x) => (
          <div key={`${x.kind}:${x.key}`} className="row recent-row" style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)", gap: 8 }}>
            {x.kind === "compare" ? <GitCompare size={13} color="var(--accent-2)" /> : <ArrowRight size={13} color="var(--text-3)" />}
            <Link to={x.href} className="ellipsis" style={{ flex: 1, minWidth: 0, fontWeight: 500 }} title={x.sub}>{x.title}</Link>
            <span className="faint" style={{ fontSize: 12 }}>{fmtAgo(new Date(x.at).toISOString())}</span>
            <button className="icon-btn recent-forget" aria-label={`Forget ${x.title}`} title="Remove from this list" onClick={() => forget(x.kind, x.key)}><X size={12} /></button>
          </div>
        ))}
      </div>
    </Card>
  );
}

/** A short greeting by the time of day — with a touch of expression when that is on (Settings → Appearance). */
function greeting(): string {
  const h = new Date().getHours();
  const [text, e] = h < 5 ? ["Working late", "🌙"] : h < 12 ? ["Good morning", "☀️"] : h < 18 ? ["Good afternoon", "🌤️"] : ["Good evening", "🌙"];
  const mark = emoji(e).trim();
  return mark ? `${text} ${mark}` : text;
}

/** The runs you watch (run page ⋯ → Watch this run): you hear when they finish, fail or develop a problem. */
function WatchingCard() {
  const [watched, setWatched] = useWatched();
  if (!watched.length) return null;
  return (
    <Card title={<span className="row"><Eye size={14} /> Watching</span>} sub={`${watched.length}`} flush>
      <div data-testid="watching-card">
        {watched.map((w) => (
          <div key={w.ref} className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)", gap: 8 }}>
            <Link to={`/runs/${encodeURIComponent(w.ref)}`} className="ellipsis" style={{ flex: 1 }}>{w.name}</Link>
            <span className="faint" style={{ fontSize: 12 }}>{w.status}</span>
            <button type="button" className="btn ghost sm icon" aria-label={`Stop watching ${w.name}`} title="Stop watching" onClick={() => setWatched(watched.filter((x) => x.ref !== w.ref))}><EyeOff size={13} /></button>
          </div>
        ))}
      </div>
    </Card>
  );
}

/** Starred experiments (Runs → ☆): one click to their runs. */
function StarredCard() {
  const { workspace } = useApp();
  const exps = useQuery({ queryKey: ["experiments", workspace], queryFn: () => api.experiments(workspace), staleTime: 60_000 });
  const starred = (exps.data ?? []).filter((e) => e.starred);
  if (!starred.length) return null;
  return (
    <Card title={<span className="row"><Star size={14} fill="var(--warn)" color="var(--warn)" /> Starred experiments</span>}
      right={<Link to="/runs?q=exp%3Astarred" className="btn ghost sm">Their runs <ArrowRight size={13} /></Link>} flush>
      <div data-testid="starred-card">
        {starred.slice(0, 6).map((e) => (
          <Link key={e.name} to={`/runs?q=${encodeURIComponent(`experiment:"${e.name}"`)}`} className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)", gap: 8 }}>
            <span className="ellipsis" style={{ flex: 1 }}>{e.name}</span>
            <span className="faint" style={{ fontSize: 12 }}>{e.runs} runs{e.last ? ` · ${fmtAgo(e.last)}` : ""}</span>
          </Link>
        ))}
      </div>
    </Card>
  );
}
