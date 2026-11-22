import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, PlugZap } from "lucide-react";
import { api } from "../api/client";
import { useApp } from "./AppState";

/** The active model with a status dot, and one button: offline → find and connect the local server
 *  (127.0.0.1:8080/v1 by default); connected → check that it still answers. */
export function LlmBadge() {
  const qc = useQueryClient();
  const { toast } = useApp();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 30_000 });
  const h = health.data;
  const offline = h ? !h.llm_enabled : true;
  const refresh = (): void => { void qc.invalidateQueries({ queryKey: ["health"] }); void qc.invalidateQueries({ queryKey: ["llm-settings"] }); };
  const act = useMutation({
    mutationFn: async () => {
      if (offline) {
        const r = await api.connectLocalLlm();
        if (r.ok) toast(`Connected to ${r.model} at ${r.base_url} (${r.latency_ms} ms)`, "good");
        else toast(r.error ?? "The local server did not answer", "bad");
        return r.ok;
      }
      const s = await api.pingLlm();
      if (s.ok) toast(`${s.model} answers (${s.latency_ms} ms)`, "good");
      else toast(`${s.model}: ${s.error ?? s.state}`, "bad");
      return s.ok;
    },
    onSettled: refresh,
  });
  const st = h?.llm_status?.state ?? (offline ? "offline" : "unknown");
  const dot = act.isPending ? "checking" : st;
  const title = offline
    ? `No LLM — answers come from rule-based checks. Click the plug to connect the local server at ${h?.local_llm_url ?? "127.0.0.1:8080/v1"}.`
    : st === "ok"
      ? `${h?.llm} answered ${h?.llm_status?.latency_ms ?? "?"} ms — checked ${h?.llm_status ? new Date(h.llm_status.at).toLocaleTimeString() : ""}. Click the plug to check again.`
      : st === "failed"
        ? `${h?.llm} did not answer: ${h?.llm_status?.error ?? ""}. Click the plug to try again.`
        : `${h?.llm} — not checked yet. Click the plug to check that it answers.`;
  return (
    <span className="llm-badge row" style={{ gap: 4 }}>
      <span className={`pill ai llm-pill ${dot}`} title={title}>
        <span className={`llm-dot ${dot}`} aria-hidden />
        {h?.llm ?? "…"}
      </span>
      <button type="button" className="icon-btn llm-plug" aria-label={offline ? "Connect the local LLM" : "Check the LLM connection"} title={title}
        disabled={act.isPending} onClick={() => act.mutate()}>
        {act.isPending ? <Loader2 size={14} className="spin" /> : <PlugZap size={14} />}
      </button>
    </span>
  );
}
