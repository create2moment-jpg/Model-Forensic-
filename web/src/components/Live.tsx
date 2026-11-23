import { useQueryClient } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocalStorage } from "../lib/hooks";

/** While a run is still running: refresh everything shown for it every few seconds (local folders 10 s, remote
 *  workspaces 30 s — the server re-reads them no faster). Read-only; switch it off to freeze the view. */
export function LiveRefresh({ runId, running, local }: { runId: string; running: boolean; local: boolean }) {
  const qc = useQueryClient();
  const [on, setOn] = useLocalStorage("mf.live", true);
  const [last, setLast] = useState(Date.now());
  const [, tick] = useState(0);
  const every = local ? 10_000 : 30_000;
  useEffect(() => {
    if (!running || !on) return;
    const id = window.setInterval(() => {
      void qc.invalidateQueries({ predicate: (q) => JSON.stringify(q.queryKey).includes(runId) });
      setLast(Date.now());
    }, every);
    const clock = window.setInterval(() => tick((x) => x + 1), 1000);
    return () => { window.clearInterval(id); window.clearInterval(clock); };
  }, [running, on, every, runId, qc]);
  if (!running) return null;
  const ago = Math.round((Date.now() - last) / 1000);
  return (
    <button type="button" className={`pill live-pill ${on ? "on" : ""}`} data-testid="live" aria-pressed={on}
      title={on ? `Live: refreshed every ${every / 1000} s while the run is running — click to freeze the view` : "Frozen — click to follow the run live"}
      onClick={() => { setOn(!on); setLast(Date.now()); }}>
      <Radio size={11} /> {on ? `live · ${ago}s ago` : "frozen"}
    </button>
  );
}
