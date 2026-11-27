/** A continuous-training track's state as a small badge, and durations as people read them — shared by the Tracks
 *  pages and Today (kept apart so Today does not load the Tracks page). */
import type { TrackState } from "../api/types";

export const TRACK_STATE: Record<TrackState, { label: string; tone: string }> = {
  best: { label: "best so far", tone: "var(--good)" },
  better: { label: "better than the best", tone: "var(--good)" },
  same: { label: "as good as the best", tone: "var(--text-2)" },
  mixed: { label: "mixed vs the best", tone: "var(--warn)" },
  worse: { label: "below the best", tone: "var(--bad)" },
  failed: { label: "latest run failed", tone: "var(--bad)" },
  no_data: { label: "no values yet", tone: "var(--text-3)" },
};

export const fmtSecs = (s: number | null | undefined): string => {
  if (s === null || s === undefined) return "—";
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  const h = Math.floor(s / 3600);
  return `${h} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")} m`;
};

export function TrackStateBadge({ state }: { state: TrackState | undefined }) {
  const s = TRACK_STATE[state ?? "no_data"];
  return <span className="track-state" data-state={state ?? "no_data"} style={{ color: s.tone, borderColor: `color-mix(in srgb, ${s.tone} 35%, transparent)` }}>{s.label}</span>;
}
