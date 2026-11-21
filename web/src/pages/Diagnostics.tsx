import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Card, Loading } from "../components/ui";
import { fmtBytes, fmtDate } from "../lib/format";

export function DiagnosticsPage() {
  const d = useQuery({ queryKey: ["diagnostics"], queryFn: api.diagnostics, refetchInterval: 5000 });
  if (!d.data) return <div className="page"><Loading /></div>;
  const lat = Object.entries(d.data.telemetry.latency).sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className="page">
      <div className="page-head"><div><h1>Diagnostics</h1><div className="sub">Internal telemetry: latency, cache, index and audit log. Refreshes every 5s.</div></div></div>
      <div className="grid g4" style={{ marginBottom: 14 }}>
        {Object.entries(d.data.index).map(([k, v]) => <div key={k} className="card stat"><div className="label">index · {k}</div><div className="value">{v.toLocaleString()}</div></div>)}
        <div className="card stat"><div className="label">artifact cache</div><div className="value">{fmtBytes(d.data.artifact_cache_bytes)}</div></div>
        <div className="card stat"><div className="label">cache hits</div><div className="value">{d.data.telemetry.counters["artifacts.cache_hits"] ?? 0}</div></div>
      </div>
      <div className="grid g2">
        <Card title="Operation latency" flush>
          <table className="t compact"><thead><tr><th>Operation</th><th className="r">Count</th><th className="r">p50</th><th className="r">p95</th></tr></thead>
            <tbody>{lat.map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="r num">{v.count}</td><td className="r num">{v.p50_ms} ms</td><td className="r num">{v.p95_ms} ms</td></tr>)}</tbody></table>
        </Card>
        <Card title="Audit log" sub="AI tool calls, downloads, provider changes" flush>
          <table className="t compact"><tbody>{d.data.audit.map((a, i) => <tr key={i}><td className="faint" style={{ whiteSpace: "nowrap" }}>{fmtDate(a.ts)}</td><td>{a.actor}</td><td className="mono">{a.action}</td><td className="mono faint ellipsis" style={{ maxWidth: 360 }}>{JSON.stringify(a.detail)}</td></tr>)}</tbody></table>
        </Card>
      </div>
    </div>
  );
}
