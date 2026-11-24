/** Runs you watch: the app tells you when one finishes, fails or develops a problem — a desktop notification when
 *  allowed, a toast in the app always. A per-browser list (nothing on the server); a run leaves it once it ends. */
import { useCallback, useEffect, useState } from "react";

export interface Watched { ref: string; name: string; status: string; state: string }

const KEY = "mf.watch";
const EVENT = "mf-watch-change";

export function readWatched(): Watched[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "[]") as Watched[];
    return Array.isArray(v) ? v.filter((x) => typeof x?.ref === "string") : [];
  } catch { return []; }
}

export function writeWatched(list: Watched[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(list)); } catch { /* this page only */ }
  window.dispatchEvent(new Event(EVENT));
}

export function useWatched(): [Watched[], (list: Watched[]) => void] {
  const [list, setList] = useState<Watched[]>(readWatched);
  useEffect(() => {
    const on = (): void => setList(readWatched());
    window.addEventListener(EVENT, on);
    window.addEventListener("storage", on);
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener("storage", on); };
  }, []);
  const set = useCallback((l: Watched[]) => { writeWatched(l); setList(l); }, []);
  return [list, set];
}

/** What changed between two readings of a watched run, as a message — or null (nothing worth a notification). */
export function change(before: Watched, now: { status: string; state: string; sentence: string }): string | null {
  if (before.status !== now.status && (now.status === "completed" || now.status === "failed" || now.status === "canceled")) {
    return now.status === "completed" ? `finished — ${now.sentence}` : `${now.status} — ${now.sentence}`;
  }
  const worse = (s: string): number => ({ problem: 2, failed: 2, attention: 1 } as Record<string, number>)[s] ?? 0;
  if (worse(now.state) > worse(before.state) && now.state === "problem") return `a problem appeared — ${now.sentence}`;
  return null;
}
