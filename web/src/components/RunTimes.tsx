/** How long a run took, as studio shows it — labelled: *Ran 9 h 04 m · ended 21 h ago · queued 4 min*; while running,
 *  *Running for 3 h 12 m* (ticks every 30 s); exact times on hover. From the run's own times (no extra request). */
import { useEffect, useState } from "react";
import type { Run } from "../api/types";
import { fmtAgo } from "../lib/format";

export const span = (from: string | null, to: string | null | number): string | null => {
  if (!from || to === null) return null;
  const s = ((typeof to === "number" ? to : new Date(to).getTime()) - new Date(from).getTime()) / 1000;
  if (!(s >= 0)) return null;
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  const h = Math.floor(s / 3600);
  return `${h} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")} m`;
};

function useNow(live: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, [live]);
  return now;
}

export function RunTimes({ run: r }: { run: Pick<Run, "status" | "created_at" | "started_at" | "ended_at"> }) {
  const running = r.status === "running";
  const now = useNow(running || r.status === "queued");
  const queued = span(r.created_at, r.started_at ?? (r.status === "queued" ? now : null));
  const ran = span(r.started_at, r.ended_at ?? (running ? now : null));
  const exact = [["created", r.created_at], ["started", r.started_at], ["ended", r.ended_at]]
    .filter(([, v]) => v).map(([k, v]) => `${k} ${new Date(v!).toLocaleString()}`).join("\n");
  const parts: string[] = [];
  if (r.status === "queued") parts.push(queued ? `queued for ${queued}` : "queued");
  else if (running) parts.push(ran ? `running for ${ran}` : "running");
  else if (ran) parts.push(`ran ${ran}`);
  if (r.ended_at) parts.push(`ended ${fmtAgo(r.ended_at)}`);
  else if (!running && r.status !== "queued") parts.push(`created ${fmtAgo(r.created_at)}`);
  const waited = r.created_at && r.started_at ? (new Date(r.started_at).getTime() - new Date(r.created_at).getTime()) / 1000 : 0;
  if (queued && r.status !== "queued" && waited >= 30) parts.push(`queued ${queued}`);  // a few seconds is not worth saying
  if (!ran && r.status !== "queued" && !running) parts.push("duration unknown");
  return <span className="run-times" data-testid="run-times" title={exact || undefined}>{parts.join(" · ").replace(/^./, (c) => c.toUpperCase())}</span>;
}
