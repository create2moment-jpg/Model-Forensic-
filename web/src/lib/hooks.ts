import { useCallback, useEffect, useRef, useState } from "react";

export function useLocalStorage<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = useCallback((v: T): void => {
    setValue(v);
    try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* storage unavailable */ }
  }, [key]);
  return [value, set];
}

export function useDebounced<T>(value: T, ms = 200): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Global keyboard shortcut. `combo` like "mod+k", "g r", "/" (sequences separated by space). */
export function useHotkey(combo: string, handler: (e: KeyboardEvent) => void, enabled = true): void {
  const seq = useRef<string[]>([]);
  const h = useRef(handler);
  h.current = handler;
  useEffect(() => {
    if (!enabled) return;
    const parts = combo.toLowerCase().split(" ");
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onKey = (e: KeyboardEvent): void => {
      const target = e.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      const key = `${e.metaKey || e.ctrlKey ? "mod+" : ""}${e.key.toLowerCase()}`;
      if (typing && !key.startsWith("mod+") && key !== "escape") return;
      seq.current = [...seq.current, key].slice(-parts.length);
      clearTimeout(timer);
      timer = setTimeout(() => (seq.current = []), 900);
      if (seq.current.join(" ") === parts.join(" ")) {
        e.preventDefault();
        seq.current = [];
        h.current(e);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [combo, enabled]);
}
