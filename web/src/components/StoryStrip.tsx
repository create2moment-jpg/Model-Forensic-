import { useQuery } from "@tanstack/react-query";
import { ArrowRight, HeartPulse, Play } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Skeleton } from "./ui";

/** One line that tells the story of a run: verdict → the top findings → the suggested next step.
 *  The check reads the run's metric histories and evaluation files (heavy on Azure ML), so it runs only when asked —
 *  or automatically when ``auto`` (Full Mode). */
export function StoryStrip({ run, compact = false, auto = false }: { run?: string | null; compact?: boolean; auto?: boolean }) {
  const [asked, setAsked] = useState(false);
  const go = auto || asked;
  const q = useQuery({ queryKey: ["check", run ?? null], queryFn: () => api.check(run ?? null), staleTime: 60_000, retry: false, enabled: go });
  if (!go && !q.data) {
    return (
      <div className="story" data-testid="story-idle">
        <HeartPulse size={15} className="faint" />
        <span className="faint" style={{ fontSize: 12.5 }}>Quick check of {run ? "this run" : "your newest run"} — reads its metric histories and evaluation files (heavy on Azure ML), so it runs when you ask.</span>
        <button className="btn sm" onClick={() => setAsked(true)}><Play size={13} /> Check {run ? "this run" : "it"}</button>
      </div>
    );
  }
  if (q.isLoading) return <div className="story"><Skeleton h={compact ? 40 : 54} /></div>;
  if (!q.data) return null;
  const d = q.data;
  const top = d.cards.slice(0, compact ? 2 : 3);
  return (
    <div className={`story ${d.overall}`}>
      <div className="story-verdict">
        <span className={`light-dot ${d.overall}`} style={{ width: 14, height: 14 }} />
        <div>
          <div style={{ fontWeight: 600 }}>{d.summary}</div>
          {!compact && <div className="faint" style={{ fontSize: 12 }}>{d.checked.length} checks · {d.cards.length + d.more} finding(s)</div>}
          {compact && <div className="faint" style={{ fontSize: 12 }}><Link to={`/runs/${encodeURIComponent(d.run.ref)}`}>{d.run.name}</Link></div>}
        </div>
      </div>
      {top.map((c, i) => (
        <div key={i} className="story-step">
          <ArrowRight size={14} className="faint" />
          <span className={`light-dot ${c.light}`} />
          <span className="ellipsis" title={c.meaning}>{c.title}</span>
        </div>
      ))}
      {top[0] && !compact && (
        <div className="story-next"><span className="faint">Next:</span> {top[0].todo}</div>
      )}
      {(d.pending ?? []).some((p) => p.files > 0) && <span className="faint" style={{ fontSize: 12 }} title="evaluation checks used the files already downloaded">· {d.pending!.reduce((a, p) => a + p.files, 0)} evaluation files not downloaded</span>}
      <Link className="btn sm ghost" to={`/check?run=${encodeURIComponent(d.run.ref)}`}><HeartPulse size={13} /> Details</Link>
    </div>
  );
}
