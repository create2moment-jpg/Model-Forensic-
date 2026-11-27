/** Summary → Details: everything studio's job Overview shows — times, who / where, environment, command with its
 *  arguments, inputs and outputs (asset, version, path), tags and every property (filterable, secrets hidden).
 *  Rendered from the run the page already has: no request of its own. */
import { Copy, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { JobIO, Run } from "../../api/types";
import { useApp } from "../../components/AppState";
import { span } from "../../components/RunTimes";
import { Card } from "../../components/ui";

/** ``azureml:name:12`` · ``…/environments/name/versions/12`` · ``name@latest`` → name and version. */
export function envParts(env: string | null): { name: string; version: string | null } | null {
  if (!env) return null;
  const arm = /\/environments\/([^/]+)\/versions\/([^/]+)$/.exec(env);
  if (arm) return { name: arm[1]!, version: arm[2]! };
  const short = /^azureml:([^:@/]+)(?::([^:@/]+)|@(\w+))?$/.exec(env);
  if (short) return { name: short[1]!, version: short[2] ?? short[3] ?? null };
  return { name: env, version: null };
}

/** ``python train.py --epochs 24 --lr 2e-4`` → the program, then one argument per line. */
export function commandLines(cmd: string | null): string[] {
  if (!cmd) return [];
  return cmd.trim().split(/\s+(?=--?[A-Za-z])/);
}

const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleString() : "—");

function Row({ k, children, mono }: { k: string; children: React.ReactNode; mono?: boolean }) {
  return <><dt>{k}</dt><dd className={mono ? "mono" : undefined}>{children}</dd></>;
}

function IoTable({ items }: { items: JobIO[] }) {
  return (
    <div className="table-wrap">
      <table className="t" data-testid="run-io">
        <thead><tr><th>Name</th><th>Type</th><th>Asset</th><th>Version</th><th>Path / value</th><th>Mode</th></tr></thead>
        <tbody>
          {items.map((x) => (
            <tr key={`${x.direction}:${x.name}`}>
              <td className="mono">{x.name}</td>
              <td className="muted">{x.type ?? "—"}</td>
              <td className="mono">{x.asset ?? "—"}</td>
              <td className="mono">{x.version ?? "—"}</td>
              <td className="mono faint ellipsis selectable" style={{ maxWidth: 420 }} title={x.path ?? x.value ?? undefined}>{x.value ?? x.path ?? "—"}</td>
              <td className="muted">{x.mode ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DetailsTab({ run: r }: { run: Run }) {
  const { toast } = useApp();
  const [filter, setFilter] = useState("");
  const env = envParts(r.environment);
  const cmd = commandLines(r.command);
  const inputs = (r.io ?? []).filter((x) => x.direction === "input");
  const outputs = (r.io ?? []).filter((x) => x.direction === "output");
  // a local run has no job inputs: its datasets stand in
  const dataIn: JobIO[] = inputs.length ? inputs : r.datasets.map((d) => ({ name: d.name, direction: "input", type: d.role, asset: d.name, version: d.version, path: d.uri, mode: null, value: null }));
  const props = useMemo(() => {
    const t = filter.trim().toLowerCase();
    return Object.entries(r.properties ?? {}).filter(([k, v]) => !t || k.toLowerCase().includes(t) || v.toLowerCase().includes(t)).sort(([a], [b]) => a.localeCompare(b));
  }, [r.properties, filter]);
  const nodes = r.compute.node_count ?? 1;
  const ran = r.started_at && r.ended_at ? (new Date(r.ended_at).getTime() - new Date(r.started_at).getTime()) / 3600_000 : null;
  const copy = (text: string, what: string): void => { void navigator.clipboard?.writeText(text); toast(`Copied ${what}`); };
  return (
    <div className="col" style={{ gap: 14 }} data-testid="run-details">
      <div className="grid g2">
        <Card title="Time">
          <dl className="kv">
            <Row k="Created">{when(r.created_at)}</Row>
            <Row k="Started">{when(r.started_at)}</Row>
            <Row k="Ended">{when(r.ended_at)}</Row>
            <Row k="Queued">{span(r.created_at, r.started_at) ?? "—"}</Row>
            <Row k="Ran">{span(r.started_at, r.ended_at ?? (r.status === "running" ? Date.now() : null)) ?? "—"}</Row>
            <Row k="Compute time">{ran !== null ? `${(ran * nodes).toFixed(1)} node-hours${r.compute.gpu_count ? ` · ${(ran * nodes * r.compute.gpu_count).toFixed(1)} GPU-hours` : ""}` : "—"}</Row>
          </dl>
        </Card>
        <Card title="Where">
          <dl className="kv">
            <Row k="Submitted by">{r.user ?? "—"}</Row>
            <Row k="Experiment">{r.experiment ?? "—"}</Row>
            <Row k="Compute">{r.compute.target ?? "—"}{r.compute.instance_type ? ` · ${r.compute.instance_type}` : ""}</Row>
            <Row k="Nodes / GPUs">{`${r.compute.node_count ?? "—"} node(s)${r.compute.gpu_count ? ` · ${r.compute.gpu_count}× ${r.compute.gpu_type ?? "GPU"}` : ""}`}</Row>
            <Row k="Environment" mono>{env ? <>{env.name}{env.version && <span className="pill outline" style={{ marginLeft: 6 }}>v{env.version}</span>}</> : "—"}</Row>
            <Row k="Code" mono>{r.git.commit ? `${r.git.branch ?? "?"} @ ${r.git.commit.slice(0, 10)}${r.git.dirty ? " (dirty)" : ""}` : "—"}</Row>
          </dl>
        </Card>
      </div>

      <Card title="Command" right={r.command && <button className="btn ghost sm" onClick={() => copy(r.command!, "the command")}><Copy size={13} /> Copy</button>}>
        {cmd.length ? <pre className="cmd-lines" data-testid="run-command">{cmd.map((l, i) => <div key={i} className={i ? "arg" : "prog"}>{l}{i < cmd.length - 1 ? " \\" : ""}</div>)}</pre> : <span className="muted">No command recorded.</span>}
      </Card>

      <Card title="Inputs" sub={`${dataIn.length}`} flush>{dataIn.length ? <IoTable items={dataIn} /> : <div className="card-body muted">No inputs recorded.</div>}</Card>
      {outputs.length > 0 && <Card title="Outputs" sub={`${outputs.length}`} flush><IoTable items={outputs} /></Card>}

      {Object.keys(r.tags).length > 0 && (
        <Card title="Tags">
          <div className="row wrap" style={{ gap: 6 }}>{Object.entries(r.tags).map(([k, v]) => <span key={k} className="tag mono">{k}={v}</span>)}</div>
        </Card>
      )}

      {Object.keys(r.properties ?? {}).length > 0 && <Card title="Properties" sub={`${Object.keys(r.properties ?? {}).length} · values of secret-looking keys are hidden`} flush
        right={<label className="input-wrap" style={{ width: 260 }}><Search size={13} /><input className="input" aria-label="Filter properties" placeholder="Filter properties…" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ height: 28, paddingLeft: 28 }} /></label>}>
        {(
          <div className="table-wrap" style={{ maxHeight: 460 }}>
            <table className="t" data-testid="run-properties">
              <tbody>
                {props.map(([k, v]) => (
                  <tr key={k}><td className="mono" style={{ width: "38%" }}>{k}</td><td className="mono selectable" style={{ overflowWrap: "anywhere", whiteSpace: "normal" }}>{v}</td></tr>
                ))}
                {props.length === 0 && <tr><td colSpan={2} className="muted">No property matches “{filter}”.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </Card>}
    </div>
  );
}
