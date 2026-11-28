import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { emoji } from "../lib/appearance";
import { change, readWatched, writeWatched } from "../lib/watch";
import { useApp } from "./AppState";

const EVERY_MS = 30_000;

/** Reads every watched run now and then (and when you come back to the tab) and tells you what changed: a desktop
 *  notification when allowed, a toast always. A remote run is read from what is loaded (never a heavy fetch); its
 *  status still comes from the run listing. */
export function RunWatcher() {
  const { toast } = useApp();
  const nav = useNavigate();
  const busy = useRef(false);
  useEffect(() => {
    const tick = async (): Promise<void> => {
      if (busy.current) return;
      busy.current = true;
      try {
        const list = readWatched();
        if (!list.length) return;
        const next = [...list];
        for (const [i, w] of list.entries()) {
          let v;
          try {
            v = await api.verdict(w.ref, null, null, true);
            if (v.state === "not_loaded" && !v.remote) v = await api.verdict(w.ref, null, null, false);
          } catch { continue; }  // unreachable now: try again next time
          const msg = change(w, { status: v.status, state: v.state, sentence: v.sentence });
          if (msg) notify(`${emoji(msg.startsWith("finished") ? "🏁" : msg.startsWith("failed") ? "🔴" : "⚠️")}${w.name}: ${msg}`, () => nav(`/runs/${encodeURIComponent(w.ref)}`), toast);
          next[i] = { ...w, status: v.status, state: v.state };
        }
        // a run that ended has been reported: it leaves the list
        writeWatched(next.filter((w) => w.status === "running" || w.status === "queued" || w.status === "preparing"));
      } finally {
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

export function notify(text: string, open: () => void, toast: (t: string, tone?: "good" | "bad") => void): void {
  toast(text, /fail|problem/.test(text) ? "bad" : "good");
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      const n = new Notification("Model Forensic", { body: text, tag: text });
      n.onclick = () => { window.focus(); open(); n.close(); };
    }
  } catch { /* notifications unavailable: the toast said it */ }
}
