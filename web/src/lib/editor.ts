/** Open a code reference (``path/to/file.py:123``) in the developer's editor. The editor and the training repository's
 *  folder on this machine are a per-browser preference (Settings → Editor); without them references stay text. */
import { useCallback, useEffect, useState } from "react";

export type EditorId = "none" | "vscode" | "cursor" | "idea";
export interface EditorPref { editor: EditorId; root: string }

export const EDITORS: { id: EditorId; label: string }[] = [
  { id: "none", label: "None (references are text)" },
  { id: "vscode", label: "VS Code" },
  { id: "cursor", label: "Cursor" },
  { id: "idea", label: "JetBrains (PyCharm, IntelliJ)" },
];

const KEY = "mf.editor";
const EVENT = "mf-editor-change";

function read(): EditorPref {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null") as EditorPref | null;
    if (v && EDITORS.some((e) => e.id === v.editor)) return { editor: v.editor, root: String(v.root ?? "") };
  } catch { /* storage blocked or bad value */ }
  return { editor: "none", root: "" };
}

export function useEditor(): [EditorPref, (p: EditorPref) => void] {
  const [pref, setPref] = useState<EditorPref>(read);
  useEffect(() => {
    const on = (): void => setPref(read());
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  const set = useCallback((p: EditorPref): void => {
    try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* this page only */ }
    setPref(p);
    window.dispatchEvent(new Event(EVENT));
  }, []);
  return [pref, set];
}

/** ``src/model/head.py:42`` (or ``…:42:7``, or a bare path) → ``{file, line}``. */
export function parseRef(ref: string): { file: string; line: number | null } {
  const m = /^(.*?):(\d+)(?::\d+)?$/.exec(ref.trim());
  return m ? { file: m[1]!, line: Number(m[2]) } : { file: ref.trim(), line: null };
}

/** The editor link for a reference, or null when no editor is set (or the path cannot be placed). */
export function editorUrl(ref: string, pref: EditorPref): string | null {
  if (pref.editor === "none") return null;
  const { file, line } = parseRef(ref);
  const abs = file.startsWith("/") || /^[A-Za-z]:[\\/]/.test(file) ? file : pref.root ? `${pref.root.replace(/[\\/]+$/, "")}/${file.replace(/^\.?\//, "")}` : null;
  if (!abs) return null;
  const path = abs.replace(/\\/g, "/");
  if (pref.editor === "idea") return `idea://open?file=${encodeURIComponent(path)}${line ? `&line=${line}` : ""}`;
  return `${pref.editor}://file${path.startsWith("/") ? "" : "/"}${encodeURI(path)}${line ? `:${line}` : ""}`;
}
