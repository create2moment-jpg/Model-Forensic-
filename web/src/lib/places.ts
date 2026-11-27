/** Where things live: the app's places and the sections inside each — the one source the sidebar, the section bar and
 *  the navigation switch read. A page belongs to a place through its route; pages never know about navigation, and
 *  every route keeps working (a section is a route, so old links and bookmarks open the same page).
 *
 *  ``simplified`` (default): five places — Today, Runs, Compare, Studies, Knowledge — each with its sections as a quiet
 *  bar above the page. ``classic``: the sidebar as it was (every page its own entry). */

import { useCallback, useEffect, useState } from "react";

export type NavMode = "simplified" | "classic";
export type PlaceId = "today" | "runs" | "compare" | "studies" | "knowledge" | "settings";

export interface Section { to: string; label: string; hint?: string }
export interface Place { id: PlaceId; label: string; sections: Section[]; also?: string[]; kbd?: string }

export const PLACES: Place[] = [
  { id: "today", label: "Today", kbd: "g h", sections: [
    { to: "/", label: "Today", hint: "your newest run answered, what needs attention, what is running" },
    { to: "/check", label: "Check a run", hint: "any run in plain language" },
  ] },
  { id: "runs", label: "Runs", kbd: "g r", also: ["/runs/", "/tracks/"], sections: [
    { to: "/runs", label: "All runs" },
    { to: "/tracks", label: "Continuous training", hint: "recurring runs of an experiment, each vs the previous and the best" },
    { to: "/local", label: "Local folders", hint: "training / evaluation folders on this machine, read in place" },
    { to: "/prefetch", label: "Offline copies", hint: "download runs' files to work without the backend" },
  ] },
  { id: "compare", label: "Compare", kbd: "g c", sections: [
    { to: "/compare", label: "Runs side by side", hint: "baseline and candidates: deltas, config diff, curves" },
    { to: "/progression", label: "Progression", hint: "a metric across many runs" },
  ] },
  { id: "studies", label: "Studies", kbd: "g t", also: ["/studies/", "/study-wizard"], sections: [
    { to: "/studies", label: "Studies" },
    { to: "/ledger", label: "Ledger", hint: "hypothesis → change → outcome → decision" },
    { to: "/test-space", label: "Test Space" },
    { to: "/reports", label: "Reports" },
  ] },
  { id: "knowledge", label: "Knowledge", sections: [
    { to: "/knowledge", label: "Knowledge" },
    { to: "/library", label: "Analyses & prompts" },
  ] },
  { id: "settings", label: "Settings", sections: [
    { to: "/settings", label: "Settings" },
    { to: "/diagnostics", label: "Connection health" },
  ] },
];

/** The place a path belongs to (a section's route, or a page under it such as a run or a study). */
export function placeOf(path: string): Place | undefined {
  const exact = PLACES.find((p) => p.sections.some((s) => s.to === path));
  if (exact) return exact;
  return PLACES.find((p) => (p.also ?? []).some((pre) => path.startsWith(pre)));
}

/** The section bar shows on a section's own route, never on a page inside it (a run, a study). */
export function sectionsAt(path: string): Section[] {
  const p = PLACES.find((x) => x.sections.some((s) => s.to === path));
  return p && p.sections.length > 1 ? p.sections : [];
}

const KEY = "mf.nav";
const EVENT = "mf-nav-change";

function read(): NavMode {
  try { return localStorage.getItem(KEY) === JSON.stringify("classic") ? "classic" : "simplified"; } catch { return "simplified"; }
}

/** The navigation mode, the same in every component (Settings changes it, the sidebar follows at once); remembered
 *  per browser — a viewing preference, nothing else depends on it. */
export function useNavMode(): [NavMode, (m: NavMode) => void] {
  const [mode, setMode] = useState<NavMode>(read);
  useEffect(() => {
    const on = (): void => setMode(read());
    window.addEventListener(EVENT, on);
    window.addEventListener("storage", on);
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener("storage", on); };
  }, []);
  const set = useCallback((m: NavMode): void => {
    try { localStorage.setItem(KEY, JSON.stringify(m)); } catch { /* storage unavailable: this page only */ }
    setMode(m);
    window.dispatchEvent(new Event(EVENT));
  }, []);
  return [mode, set];
}
