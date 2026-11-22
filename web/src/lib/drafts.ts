import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

export type DraftStatus = "idle" | "loading" | "saving" | "saved" | "error";

const fresh = new Set<string>();  // ids created in this tab and not saved yet: nothing to load

export const newDraftId = (kind: string): string => {
  const id = `${kind}:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
  fresh.add(id);
  return id;
};

/** A form's state kept as a server draft: loaded when ``id`` is set, autosaved (debounced) while you type, flushed
 *  when you leave the page — so half-finished work survives other pages, restarts and other browsers.
 *  ``isEmpty`` drafts are not saved; ``describe`` gives the title and the route that resumes it. */
export function useServerDraft<T>(
  id: string | null,
  kind: string,
  initial: T,
  describe: (v: T) => { title: string; route: string },
  isEmpty: (v: T) => boolean,
): { value: T; set: (v: T) => void; status: DraftStatus; savedAt: Date | null; discard: () => Promise<void>; ready: boolean } {
  const qc = useQueryClient();
  const [value, setValue] = useState<T>(initial);
  const [status, setStatus] = useState<DraftStatus>(id ? "loading" : "idle");
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const ready = !id || loadedId === id;  // derived, so it is false in the very render the id changes
  const pending = useRef<{ id: string; v: T } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const describeRef = useRef(describe);
  describeRef.current = describe;

  const flush = useCallback(async (): Promise<void> => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    const p = pending.current;
    pending.current = null;
    if (!p) return;
    setStatus("saving");
    try {
      await api.saveDraft(p.id, { kind, ...describeRef.current(p.v), payload: p.v });
      fresh.delete(p.id);
      setStatus("saved");
      setSavedAt(new Date());
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      void qc.invalidateQueries({ queryKey: ["work"] });
    } catch {
      setStatus("error");
    }
  }, [kind, qc]);

  // Load when the id changes.
  useEffect(() => {
    let alive = true;
    if (!id) { setValue(initial); setStatus("idle"); return; }
    if (fresh.has(id)) { setValue(initial); setStatus("idle"); setLoadedId(id); return; }
    setStatus("loading");
    api.draft<T>(id).then((d) => {
      if (!alive) return;
      setValue({ ...initial, ...d.payload });
      setSavedAt(new Date(d.updated_at));
      setStatus("saved");
    }).catch((e: unknown) => {
      if (!alive) return;
      setValue(initial);
      setStatus(e instanceof ApiError && e.status === 404 ? "idle" : "error");
    }).finally(() => { if (alive) setLoadedId(id); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // Save what is pending when the page is left or another draft is opened (pending saves carry their own id).
  useEffect(() => () => { void flush(); }, [id, flush]);

  const set = useCallback((v: T): void => {
    setValue(v);
    if (!id || loadedId !== id || isEmpty(v)) return;  // never save over a draft that has not loaded yet
    pending.current = { id, v };
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), 700);
  }, [id, loadedId, isEmpty, flush]);

  const discard = useCallback(async (): Promise<void> => {
    pending.current = null;
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    if (id) { try { await api.deleteDraft(id); } catch { /* already gone */ } }
    setValue(initial);
    setStatus("idle");
    setSavedAt(null);
    void qc.invalidateQueries({ queryKey: ["drafts"] });
    void qc.invalidateQueries({ queryKey: ["work"] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, qc]);

  return { value, set, status, savedAt, discard, ready };
}

/** One-time move of a draft kept in this browser (older versions) to the server. */
export function takeLocalDraft<T>(keys: string[], build: (read: (k: string) => unknown) => T | null): T | null {
  try {
    const read = (k: string): unknown => { const raw = localStorage.getItem(k); return raw === null ? undefined : JSON.parse(raw); };
    const v = build(read);
    keys.forEach((k) => localStorage.removeItem(k));
    return v;
  } catch {
    return null;
  }
}
