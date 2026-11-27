import { useQuery } from "@tanstack/react-query";
import { Copy, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../../api/client";
import { useApp } from "../../components/AppState";
import { Card, Loading, Switch } from "../../components/ui";
import { fmtValue } from "../../lib/format";

export function ConfigTab({ runId, baseline }: { runId: string; baseline: string | null }) {
  const { toast } = useApp();
  const cfg = useQuery({ queryKey: ["config", runId], queryFn: () => api.flatConfig(runId) });
  const base = useQuery({ queryKey: ["config", baseline], queryFn: () => api.flatConfig(baseline!), enabled: !!baseline && baseline !== runId });
  const [filter, setFilter] = useState("");
  const [diffOnly, setDiffOnly] = useState(false);
  const rows = useMemo(() => {
    const a = cfg.data ?? {};
    const b = base.data;
    const keys = [...new Set([...Object.keys(a), ...Object.keys(b ?? {})])].sort();
    return keys.map((k) => {
      const va = fmtValue(a[k]);
      const vb = b ? fmtValue(b[k]) : null;
      return { k, va: k in a ? va : null, vb: b && k in b ? vb : null, diff: !!b && va !== vb };
    }).filter((r) => (!filter || r.k.toLowerCase().includes(filter.toLowerCase()) || String(r.va).toLowerCase().includes(filter.toLowerCase())) && (!diffOnly || r.diff));
  }, [cfg.data, base.data, filter, diffOnly]);
  if (cfg.isLoading) return <Loading />;
  const hasBase = !!base.data;
  return (
    <Card title="Configuration" sub={`${rows.length} keys · merged from all config sources (flattened)`}
      right={<>
        <div className="input-wrap" style={{ width: 260 }}><Search size={13} /><input className="input" placeholder="Filter keys or values" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
        {hasBase && <span className="row muted">Differences only <Switch on={diffOnly} onChange={setDiffOnly} label="Differences only" /></span>}
        <button className="btn sm" onClick={() => { void navigator.clipboard.writeText(JSON.stringify(cfg.data, null, 2)); toast("Config copied as JSON"); }}><Copy size={13} /> JSON</button>
      </>} flush>
      <div className="table-wrap" style={{ maxHeight: "calc(100vh - 300px)" }}>
        <table className="t compact">
          <thead><tr><th>Key</th><th>This run</th>{hasBase && <th>Baseline <span className="mono" style={{ textTransform: "none" }}>{baseline?.split(":").pop()}</span></th>}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.k} style={r.diff ? { background: "var(--warn-bg)" } : undefined}>
                <td className="mono">{r.k}</td>
                <td className="mono wrap" style={{ maxWidth: 520, overflowWrap: "anywhere" }}>{r.va ?? <span className="faint">(absent)</span>}</td>
                {hasBase && <td className="mono faint wrap" style={{ maxWidth: 520, overflowWrap: "anywhere" }}>{r.vb ?? "(absent)"}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
