/** What you opened lately — runs and comparisons, newest first — so going back is one keystroke (⌘K with nothing typed)
 *  or one click (Today). A per-browser list, nothing on the server; the link keeps the view you left (tab, baseline,
 *  stage). Also: the stage you last picked per pipeline, so a pipeline opens where you were. */
import { useEffect, useState } from "react";

export interface Recent { kind: "run" | "compare"; key: string; title: string; sub?: string; href: string; at: number }

const KEY = "mf.recent";
const EVENT = "mf-recent-change";
const MAX = 15;

export function readRecent(): Recent[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "[]") as Recent[];
    return Array.isArray(v) ? v.filter((x) => typeof x?.key === "string" && typeof x?.href === "string") : [];
  } catch { return []; }
}

/** Put an item first (the same run or comparison moves up, with the view it is in now). */
export function remember(item: Omit<Recent, "at">, now = Date.now()): Recent[] {
  const list = [{ ...item, at: now }, ...readRecent().filter((x) => !(x.kind === item.kind && x.key === item.key))].slice(0, MAX);
  try { localStorage.setItem(KEY, JSON.stringify(list)); } catch { /* storage unavailable: nothing remembered */ }
  window.dispatchEvent(new Event(EVENT));
  return list;
}

export function forget(kind: Recent["kind"], key: string): void {
  try { localStorage.setItem(KEY, JSON.stringify(readRecent().filter((x) => !(x.kind === kind && x.key === key)))); } catch { /* */ }
  window.dispatchEvent(new Event(EVENT));
}

export function useRecent(): Recent[] {
  const [list, setList] = useState<Recent[]>(readRecent);
  useEffect(() => {
    const on = (): void => setList(readRecent());
    window.addEventListener(EVENT, on);
    window.addEventListener("storage", on);
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener("storage", on); };
  }, []);
  return list;
}

const STAGE_KEY = "mf.stage.";

/** The stage last picked on this pipeline ("" = none / the whole run). */
export function lastStage(run: string): string {
  try { return localStorage.getItem(STAGE_KEY + run) ?? ""; } catch { return ""; }
}

export function rememberStage(run: string, stage: string): void {
  try { if (stage) localStorage.setItem(STAGE_KEY + run, stage); else localStorage.removeItem(STAGE_KEY + run); } catch { /* */ }
}
