/** How the app looks: the theme, expressive touches (a few emoji where they help) and motion. A per-browser viewing
 *  preference, applied as attributes on <html> (``data-theme``, ``data-mode``, ``data-motion``, ``data-expressive``)
 *  so CSS does the work; the same in every component (Settings changes it, everything follows at once). */
import { useCallback, useEffect, useState } from "react";

export type ThemeId = "midnight" | "graphite" | "ocean" | "ember" | "obsidian" | "daylight";
export interface Theme { id: ThemeId; label: string; mode: "dark" | "light"; note: string; swatch: [string, string, string] }

export const THEMES: Theme[] = [
  { id: "midnight", label: "Midnight", mode: "dark", note: "deep blue-black, violet accent (the default)", swatch: ["#0b0d12", "#151923", "#7c6cff"] },
  { id: "graphite", label: "Graphite", mode: "dark", note: "neutral greys, teal accent — quiet and focused", swatch: ["#0e0f11", "#1a1c20", "#2dd4bf"] },
  { id: "ocean", label: "Ocean", mode: "dark", note: "navy depths, sky-blue accent", swatch: ["#07101d", "#0f1d33", "#38bdf8"] },
  { id: "ember", label: "Ember", mode: "dark", note: "warm charcoal, coral accent", swatch: ["#110d0b", "#1e1714", "#ff7a59"] },
  { id: "obsidian", label: "Obsidian", mode: "dark", note: "true black for OLED screens, lavender accent", swatch: ["#000000", "#0d0d10", "#a78bfa"] },
  { id: "daylight", label: "Daylight", mode: "light", note: "light, for bright rooms and printing", swatch: ["#f6f7fb", "#ffffff", "#5b4bf0"] },
];

export interface Appearance { theme: ThemeId; expressive: boolean; motion: boolean }
const DEFAULT: Appearance = { theme: "midnight", expressive: true, motion: true };
const KEY = "mf.appearance";
const EVENT = "mf-appearance-change";

export function readAppearance(): Appearance {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<Appearance> | null;
    if (v) return { ...DEFAULT, ...v, theme: THEMES.some((t) => t.id === v.theme) ? v.theme! : DEFAULT.theme };
    const old = JSON.parse(localStorage.getItem("mf.theme") ?? "null") as string | null;  // the earlier dark / light switch
    if (old === "light") return { ...DEFAULT, theme: "daylight" };
  } catch { /* storage blocked */ }
  return DEFAULT;
}

export function applyAppearance(a: Appearance): void {
  const el = document.documentElement;
  const t = THEMES.find((x) => x.id === a.theme) ?? THEMES[0]!;
  el.dataset.theme = t.id;
  el.dataset.mode = t.mode;
  el.dataset.motion = a.motion ? "on" : "off";
  el.dataset.expressive = a.expressive ? "on" : "off";
}

export function useAppearance(): [Appearance, (a: Appearance) => void] {
  const [a, setA] = useState<Appearance>(readAppearance);
  useEffect(() => {
    const on = (): void => setA(readAppearance());
    window.addEventListener(EVENT, on);
    window.addEventListener("storage", on);
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener("storage", on); };
  }, []);
  const set = useCallback((next: Appearance): void => {
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* this page only */ }
    applyAppearance(next);
    setA(next);
    window.dispatchEvent(new Event(EVENT));
  }, []);
  return [a, set];
}

/** A touch of expression where it helps (``emoji("🎉")``) — nothing when the user prefers it plain. */
export function emoji(e: string): string {
  try { return document.documentElement.dataset.expressive === "off" ? "" : `${e} `; } catch { return ""; }
}

/** The dark theme to return to from Daylight (the sidebar's quick switch). */
export function lastDark(a: Appearance): ThemeId {
  try { const v = localStorage.getItem("mf.appearance.dark"); if (v && THEMES.some((t) => t.id === v && t.mode === "dark")) return v as ThemeId; } catch { /* default */ }
  return a.theme !== "daylight" ? a.theme : "midnight";
}
