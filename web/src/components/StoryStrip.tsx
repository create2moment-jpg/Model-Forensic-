import { useQuery } from "@tanstack/react-query";
import { ArrowRight, HeartPulse } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Skeleton } from "./ui";

/** One line that tells the story of a run: verdict → the top findings → the suggested next step. */
export function StoryStrip({ run, compact = false }: { run?: string | null; compact?: boolean }) {
  const q = useQuery({ queryKey: ["check", run ?? null], queryFn: () => api.check(run ?? null), staleTime: 60_000, retry: false });
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
      <Link className="btn sm ghost" to={`/check?run=${encodeURIComponent(d.run.ref)}`}><HeartPulse size={13} /> Details</Link>
    </div>
  );
}
