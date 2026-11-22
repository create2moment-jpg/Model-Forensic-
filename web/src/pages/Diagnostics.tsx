import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Card, Loading, Pill } from "../components/ui";
import { DiagnosisBox } from "../components/ConnectionTest";
import { fmtBytes, fmtDate } from "../lib/format";

export function DiagnosticsPage() {
  const d = useQuery({ queryKey: ["diagnostics"], queryFn: api.diagnostics, refetchInterval: 5000 });
  const h = useQuery({ queryKey: ["health-deep"], queryFn: api.healthDeep, refetchInterval: 10000 });
  const fb = useQuery({ queryKey: ["feedback-report"], queryFn: api.feedbackReport });
  if (!d.data) return <div className="page"><Loading /></div>;
  const lat = Object.entries(d.data.telemetry.latency).sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className="page">
      <div className="page-head"><div><h1>Diagnostics</h1><div className="sub">Internal telemetry: latency, cache, index and audit log. Refreshes every 5s.</div></div></div>
      <ChecksCard />
      {h.data && (
        <Card title="Status" sub={`v${h.data.version} · up ${Math.round(h.data.uptime_s / 60)} min · ${h.data.jobs_running} job(s) running`}
          right={<Pill tone={h.data.status === "ok" ? "good" : h.data.status === "degraded" ? "warn" : "bad"}>{h.data.status}</Pill>}>
          <div className="col" style={{ gap: 8 }}>
            {h.data.backends.map((b) => (
              <div key={b.name}>
                <div className="row" style={{ gap: 8 }}><b className="mono">{b.name}</b>
                  <Pill tone={b.ok ? (b.verified ? "good" : "outline") : "bad"}>{b.ok ? (b.verified ? "connected" : "not synced yet") : "failing"}</Pill>
                  {b.error && <span className="faint mono ellipsis" style={{ maxWidth: 600 }} title={b.error}>{b.error}</span>}</div>
                {b.diagnosis && <DiagnosisBox d={b.diagnosis} />}
              </div>
            ))}
            <div className="faint">Disk free {h.data.disk.free_gb ?? "?"} GB{h.data.low_disk && " — low!"} · last background sync {h.data.scheduler.last_sync ?? "—"} · LLM {h.data.llm.model}</div>
            {h.data.notices.map((n) => <div key={n} className="faint">⚠ {n}</div>)}
          </div>
        </Card>
      )}
      <div className="grid g4" style={{ margin: "14px 0" }}>
        {Object.entries(d.data.index).map(([k, v]) => <div key={k} className="card stat"><div className="label">index · {k}</div><div className="value">{v.toLocaleString()}</div></div>)}
        <div className="card stat"><div className="label">artifact cache</div><div className="value">{fmtBytes(d.data.artifact_cache_bytes)}</div></div>
        <div className="card stat"><div className="label">cache hits</div><div className="value">{d.data.telemetry.counters["artifacts.cache_hits"] ?? 0}</div></div>
      </div>
      <div className="grid g2">
        <Card title="Operation latency" flush>
          <table className="t compact"><thead><tr><th>Operation</th><th className="r">Count</th><th className="r">p50</th><th className="r">p95</th></tr></thead>
            <tbody>{lat.map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="r num">{v.count}</td><td className="r num">{v.p50_ms} ms</td><td className="r num">{v.p95_ms} ms</td></tr>)}</tbody></table>
        </Card>
        <Card title="Finding feedback" sub="‘Is this finding right?’ answers per analysis — compare with docs/calibration.md" flush>
          {fb.data && fb.data.analyses.length > 0 ? (
            <table className="t compact"><thead><tr><th>Analysis</th><th className="r">Correct</th><th className="r">Wrong</th><th className="r">False-alarm rate</th></tr></thead>
              <tbody>{fb.data.analyses.map((a) => <tr key={a.analysis} title={a.examples.map((x) => `${x.title}${x.note ? ` — ${x.note}` : ""}`).join("\n")}><td className="mono">{a.analysis}</td><td className="r num">{a.correct}</td><td className="r num">{a.wrong}</td><td className="r num">{a.false_alarm_rate == null ? "—" : `${Math.round(a.false_alarm_rate * 100)} %`}</td></tr>)}</tbody></table>
          ) : <div className="faint" style={{ padding: 14 }}>No feedback yet — use “Is this finding right?” on any finding.</div>}
        </Card>
        <Card title="Audit log" sub="AI tool calls, downloads, provider changes" flush>
          <table className="t compact"><tbody>{d.data.audit.map((a, i) => <tr key={i}><td className="faint" style={{ whiteSpace: "nowrap" }}>{fmtDate(a.ts)}</td><td>{a.actor}</td><td className="mono">{a.action}</td><td className="mono faint ellipsis" style={{ maxWidth: 360 }}>{JSON.stringify(a.detail)}</td></tr>)}</tbody></table>
        </Card>
      </div>
    </div>
  );
}

const STATUS_TONE: Record<string, string> = { pass: "good", fail: "bad", unknown: "warn", skip: "outline" };

/** Configuration checks: id · severity · current vs expected · fix · the repair prompt · how to validate. */
function ChecksCard() {
  const q = useQuery({ queryKey: ["config-checks"], queryFn: api.configChecks, refetchInterval: 30_000 });
  if (!q.data) return null;
  const c = q.data;
  const order = { fail: 0, unknown: 1, pass: 2, skip: 3 } as const;
  return (
    <Card title="Configuration checks" sub="Each failing check names its fix, the local-LLM repair prompt (setup-agent/prompts/) and how to validate it · also: mf checks"
      right={<Pill tone={c.ok ? (c.failed ? "warn" : "good") : "bad"}>{c.failed ? `${c.failed} failing${c.critical ? ` · ${c.critical} critical` : ""}` : "all pass"}</Pill>}>
      <div className="col" style={{ gap: 6 }} data-testid="config-checks">
        {[...c.checks].sort((a, b) => order[a.status] - order[b.status]).map((k) => (
          <div key={k.id} className={k.status === "fail" ? "note warn" : "row"} data-check={k.id} data-status={k.status} style={{ gap: 8, alignItems: "flex-start" }}>
            <Pill tone={STATUS_TONE[k.status] ?? "outline"}>{k.status}</Pill>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div><b className="mono">{k.id}</b> <span className="faint">[{k.severity}]</span> {k.title} — <span className="faint">{k.current}</span></div>
              {k.status === "fail" && (
                <div style={{ fontSize: 12, marginTop: 2 }}>
                  <div>Expected: {k.expected}</div><div>Fix: {k.remediation}</div>
                  <div className="faint">Prompt: <span className="mono">{k.prompt_path}</span> · Validate: <span className="mono">{k.validation}</span></div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
