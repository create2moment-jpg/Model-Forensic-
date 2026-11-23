import { FileHeart, Loader2 } from "lucide-react";
import { useState } from "react";
import { useApp } from "./AppState";

/** Build the analysis dossier (everything Model Forensic found, with evidence ids and the reader's instructions),
 *  save it to the knowledge inbox for the local LLM, and download a copy. */
export function useDossier(run: string, baseline?: string | null, stage?: string | null): { go: () => Promise<void>; busy: boolean } {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const go = async (): Promise<void> => {
    setBusy(true);
    try {
      const qs = new URLSearchParams({ save: "true", ...(baseline ? { baseline } : {}), ...(stage ? { stage } : {}) });
      const res = await fetch(`/api/v1/runs/${encodeURIComponent(run)}/dossier?${qs}`);
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const text = await res.text();
      const path = res.headers.get("X-Dossier-Path") ?? "";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
      a.download = path.split("/").pop() || "dossier.md";
      a.click();
      URL.revokeObjectURL(a.href);
      toast(`Dossier saved: ${path} — give it to your local LLM`, "good");
    } catch (e) {
      toast(String(e), "bad");
    } finally {
      setBusy(false);
    }
  };
  return { go, busy };
}

export function DossierButton({ run, baseline, stage }: { run: string; baseline?: string | null; stage?: string | null }) {
  const { go, busy } = useDossier(run, baseline, stage);
  return (
    <button className="btn" disabled={busy} onClick={() => void go()}
      title="Everything Model Forensic found about this run (and the baseline), with evidence ids and the instructions for the reader — for your local LLM to interpret">
      {busy ? <Loader2 size={14} className="spin" /> : <FileHeart size={14} />} Dossier for the LLM
    </button>
  );
}
