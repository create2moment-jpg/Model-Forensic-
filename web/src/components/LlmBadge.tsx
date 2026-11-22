import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, PlugZap } from "lucide-react";
import { useState } from "react";
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
  const [ask, setAsk] = useState(false);
  const [url, setUrl] = useState("");
  const refresh = (): void => { void qc.invalidateQueries({ queryKey: ["health"] }); void qc.invalidateQueries({ queryKey: ["llm-settings"] }); };
  const act = useMutation({
    mutationFn: async (address?: string) => {
      if (offline) {
        const r = await api.connectLocalLlm(address);
        if (r.needs_url) { setAsk(true); return false; }
        setAsk(false);
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
    ? `No LLM — answers come from rule-based checks. Click the plug to connect your local server${h?.local_llm_url ? ` at ${h.local_llm_url}` : " (you will be asked for its address once)"}.`
    : st === "ok"
      ? `${h?.llm} answered ${h?.llm_status?.latency_ms ?? "?"} ms — checked ${h?.llm_status ? new Date(h.llm_status.at).toLocaleTimeString() : ""}. Click the plug to check again.`
      : st === "failed"
        ? `${h?.llm} did not answer: ${h?.llm_status?.error ?? ""}. Click the plug to try again.`
        : `${h?.llm} — not checked yet. Click the plug to check that it answers.`;
  return (
    <span className="llm-badge row" style={{ gap: 4, position: "relative" }}>
      <span className={`pill ai llm-pill ${dot}`} title={title}>
        <span className={`llm-dot ${dot}`} aria-hidden />
        {h?.llm ?? "…"}
      </span>
      <button type="button" className="icon-btn llm-plug" aria-label={offline ? "Connect the local LLM" : "Check the LLM connection"} title={title}
        disabled={act.isPending} onClick={() => act.mutate(undefined)}>
        {act.isPending ? <Loader2 size={14} className="spin" /> : <PlugZap size={14} />}
      </button>
      {ask && (
        <form className="llm-ask card" onSubmit={(e) => { e.preventDefault(); if (url.trim()) act.mutate(url.trim()); }}>
          <div style={{ fontSize: 12.5, marginBottom: 6 }}>Address of your local OpenAI-compatible server — remembered after it answers.</div>
          <div className="row" style={{ gap: 6 }}>
            <input className="input mono" autoFocus aria-label="Local LLM address" placeholder="host:port/v1, e.g. 127.0.0.1:8080/v1" value={url} onChange={(e) => setUrl(e.target.value)} style={{ width: 260 }} />
            <button className="btn sm primary" type="submit" disabled={!url.trim() || act.isPending}>Connect</button>
            <button className="btn sm ghost" type="button" onClick={() => setAsk(false)}>Cancel</button>
          </div>
        </form>
      )}
    </span>
  );
}
