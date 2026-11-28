/** Continuous training, watched: every few minutes (and when you come back to the tab) the followed tracks are read
 *  — those *On Today* — and a new run is reported when it **fails**, falls **below the best**, or is the **new best**
 *  (a desktop notification when allowed, a toast always). The first reading of a track only remembers it. */
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Track } from "../api/types";
import { emoji } from "../lib/appearance";
import { useApp } from "./AppState";
import { notify } from "./RunWatcher";

const EVERY_MS = 5 * 60_000;
const KEY = "mf.tracks.seen";

interface Seen { latest: string | null; failed: number; runs: number }

function read(): Record<string, Seen> {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "{}") as Record<string, Seen>; } catch { return {}; }
}

/** What to say about a track that changed since it was last seen (null: nothing worth a notification). */
export function trackChange(before: Seen, t: Track): string | null {
  const h = t.health;
  if (!h) return null;
  if (h.failed > before.failed && t.state === "failed") return `${emoji("🔴")}${t.title}: the newest run failed`;
  if (t.latest && t.latest !== before.latest) {
    if (t.state === "best") return `${emoji("🏆")}${t.title}: a new best — ${t.sentence ?? ""}`;
    if (t.state === "worse" || t.state === "mixed") return `${emoji("⚠️")}${t.title}: the new run is below the best — ${t.sentence ?? ""}`;
  }
  return null;
}

export function TrackWatcher() {
  const { toast } = useApp();
  const nav = useNavigate();
  const busy = useRef(false);
  useEffect(() => {
    const tick = async (): Promise<void> => {
      if (busy.current) return;
      busy.current = true;
      try {
        const tracks = (await api.tracks(true)).tracks.filter((t) => t.watch);
        if (!tracks.length) return;
        const seen = read();
        for (const t of tracks) {
          const before = seen[t.id];
          if (before) {
            const msg = trackChange(before, t);
            if (msg) notify(msg, () => nav(`/tracks/${encodeURIComponent(t.id)}`), toast);
          }
          seen[t.id] = { latest: t.latest ?? null, failed: t.health?.failed ?? 0, runs: t.health?.runs ?? 0 };
        }
        try { localStorage.setItem(KEY, JSON.stringify(seen)); } catch { /* this tab only */ }
      } catch { /* unreachable now: try again next time */ } finally {
        busy.current = false;
      }
    };
    const id = window.setInterval(() => void tick(), EVERY_MS);
    const now = (): void => { if (document.visibilityState === "visible") void tick(); };
    window.addEventListener("focus", now);
    document.addEventListener("visibilitychange", now);
    void tick();
    return () => { window.clearInterval(id); window.removeEventListener("focus", now); document.removeEventListener("visibilitychange", now); };
  }, [nav, toast]);
  return null;
}
