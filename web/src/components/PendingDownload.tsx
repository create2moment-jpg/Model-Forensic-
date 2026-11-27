import { CloudDownload } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { planAndOpen } from "../lib/prefetch";
import { fmtBytes } from "../lib/format";
import { useApp } from "./AppState";

export interface Pending { profile: string; files: number; bytes: number; unknown_sizes?: number; total_files?: number }

const WHAT: Record<string, string> = { eval_json: "evaluation JSON file", eval_parquet: "per-sequence table" };

/** Files a view would read that are not downloaded. Nothing heavy is fetched by opening a view: this says what is
 *  missing and plans the download (review it on the Prefetch page) only when you press the button. */
export function PendingDownload({ run, pending }: { run: string; pending: Pending | Pending[] | undefined }) {
  const nav = useNavigate();
  const { toast } = useApp();
  const items = (Array.isArray(pending) ? pending : pending ? [pending] : []).filter((p) => p.files > 0);
  if (!items.length) return null;
  return (
    <div className="col" style={{ gap: 6, marginBottom: 10 }} data-testid="pending-download">
      {items.map((p) => (
        <div key={p.profile} className="note">
          <CloudDownload size={14} style={{ marginTop: 2, flex: "none" }} />
          <span style={{ flex: 1 }}>
            {p.files}{p.total_files && p.total_files !== p.files ? ` of ${p.total_files}` : ""} {WHAT[p.profile] ?? p.profile}{p.files === 1 ? "" : "s"} ({fmtBytes(p.bytes)}{p.unknown_sizes ? ` + ${p.unknown_sizes} of unknown size` : ""}) not downloaded — this view shows what is. Nothing is downloaded unless you ask.
          </span>
          <button className="btn sm" onClick={() => void planAndOpen(nav, { run, profile: p.profile }).catch((e: unknown) => toast(String(e), "bad"))}>Plan download</button>
        </div>
      ))}
    </div>
  );
}
