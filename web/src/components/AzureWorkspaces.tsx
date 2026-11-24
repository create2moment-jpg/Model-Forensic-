import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, ExternalLink, Loader2, Plus, Radar, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ConnectionTest } from "./ConnectionTest";
import type { AzureDefaultsCfg, AzureWorkspaceCfg } from "../api/types";
import { useApp } from "./AppState";
import { Card, ErrorBox, Loading, Pill } from "./ui";

const EMPTY: AzureWorkspaceCfg = { name: "", title: "", subscription_id: "", resource_group: "", workspace: "", default: false };
const DEFAULTS: AzureDefaultsCfg = { credential: "cli", tenant_id: null, metrics_api: "runhistory", fast_listing: true };
const slug = (s: string): string => s.toLowerCase().replace(/[^a-z0-9_-]/g, "-").slice(0, 41);

/** Settings → Azure ML workspaces: detect from `az login`, edit, save to the git-ignored config/azure.local.yaml. */
export function AzureWorkspaces() {
  const qc = useQueryClient();
  const { toast, trackJob } = useApp();
  const cfg = useQuery({ queryKey: ["azure-settings"], queryFn: api.azureSettings });
  const backends = useQuery({ queryKey: ["backends"], queryFn: api.backends });
  const [rows, setRows] = useState<AzureWorkspaceCfg[] | null>(null);
  const [defaults, setDefaults] = useState<AzureDefaultsCfg>(DEFAULTS);
  const [picking, setPicking] = useState(false);
  useEffect(() => {
    if (cfg.data && rows === null) {
      setRows(cfg.data.workspaces.map(({ placeholder: _p, studio_url: _s, ...w }) => w));
      setDefaults({ ...DEFAULTS, ...cfg.data.defaults });
    }
  }, [cfg.data, rows]);

  const detect = useMutation({
    mutationFn: () => api.detectAzure(),
    onSuccess: (d) => {
      const have = new Set((rows ?? []).map((r) => `${r.subscription_id}/${r.workspace}`));
      const add = d.workspaces.filter((w) => !have.has(`${w.subscription_id}/${w.workspace}`)).map(({ location: _l, ...w }) => w);
      setRows([...(rows ?? []).filter((r) => !r.placeholder), ...add]);
      const me = [d.user.name, d.user.mail, d.user.upn].filter((x): x is string => !!x);
      setDefaults({ ...defaults, ...(d.tenant_id && !defaults.tenant_id ? { tenant_id: d.tenant_id } : {}), ...(!defaults.me?.length && me.length ? { me } : {}) });
      toast(`${d.workspaces.length} workspace(s) in ${d.subscription_name ?? d.subscription_id} — review and save`, "good");
    },
  });
  const save = useMutation({
    mutationFn: () => api.saveAzure({ defaults, workspaces: rows ?? [] }),
    onSuccess: (d) => {
      setRows(d.workspaces.map(({ placeholder: _p, studio_url: _s, ...w }) => w));
      void qc.invalidateQueries({ queryKey: ["backends"] });
      void qc.invalidateQueries({ queryKey: ["azure-settings"] });
      void qc.invalidateQueries({ queryKey: ["health"] });
      toast(`Saved to ${d.path} and reconnected — new workspaces sync now`, "good");
      // a workspace with nothing indexed yet syncs now: its runs appear without restarting or running mf sync
      void api.backends().then((bs) => bs.filter((b) => b.kind === "azureml" && b.available && !b.runs && !b.syncing)
        .forEach((b) => void api.sync(b.name).then((r) => trackJob(r.job_id))));
    },
  });

  if (cfg.isLoading || rows === null) return <Card title="Azure ML workspaces"><Loading /></Card>;
  const set = (i: number, patch: Partial<AzureWorkspaceCfg>): void => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : patch.default ? { ...r, default: false } : r)));
  const placeholders = cfg.data?.workspaces.filter((w) => w.placeholder).map((w) => w.name) ?? [];

  return (
    <Card
      title={<span className="row"><Cloud size={15} color="var(--accent)" /> Azure ML workspaces</span>}
      right={<span className="faint mono" title="git-ignored local file">{cfg.data?.path}</span>}
    >
      <div className="col" style={{ gap: 12 }}>
        <div className="faint">
          Your subscription, resource groups and workspaces live only in this local, git-ignored file (or the <code>MF_AZURE_*</code> environment
          variables). Nothing is a secret: authentication uses <code>az login</code>, the default credential chain or a managed identity.
        </div>
        {cfg.data?.error && <ErrorBox error={new Error(cfg.data.error)} />}
        {placeholders.length > 0 && <div className="note">Still placeholders (skipped): <span className="mono">{placeholders.join(", ")}</span></div>}
        <div className="row wrap" style={{ gap: 8 }}>
          <button className="btn" disabled={detect.isPending} onClick={() => detect.mutate()}>
            {detect.isPending ? <Loader2 size={14} className="spin" /> : <Radar size={14} />} Detect from az login
          </button>
          <button className="btn" onClick={() => setPicking(!picking)} aria-expanded={picking}><Cloud size={14} /> Add from my Azure account…</button>
          <button className="btn" onClick={() => setRows([...rows, { ...EMPTY, default: rows.length === 0 }])}><Plus size={14} /> Add manually</button>
          <span className="spacer" />
          <label className="row faint" style={{ gap: 6 }}>Credential
            <select className="select" value={defaults.credential} onChange={(e) => setDefaults({ ...defaults, credential: e.target.value as AzureDefaultsCfg["credential"] })}>
              <option value="cli">az login (recommended)</option><option value="default">default chain</option><option value="managed_identity">managed identity</option>
            </select>
          </label>
          <label className="row faint" style={{ gap: 6 }} title="How you appear in the runs' user column — enables user:me and the 'Mine' filter">Me
            <input className="input" style={{ width: 240 }} placeholder="Your Name, you@company.com" value={(defaults.me ?? []).join(", ")}
              onChange={(e) => setDefaults({ ...defaults, me: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
          </label>
          <label className="row faint" style={{ gap: 6 }}>Metrics
            <select className="select" value={defaults.metrics_api} onChange={(e) => setDefaults({ ...defaults, metrics_api: e.target.value as AzureDefaultsCfg["metrics_api"] })}>
              <option value="runhistory">run history (pipelines)</option><option value="mlflow">MLflow</option>
            </select>
          </label>
        </div>
        {detect.error && <ErrorBox error={detect.error} />}
        {picking && <WorkspacePicker have={rows} onAdd={(w) => { setRows([...rows.filter((r) => !r.placeholder), { ...w, default: rows.length === 0 }]); toast(`Added ${w.workspace} — save to connect it`, "good"); }} />}
        {rows.length > 0 ? (
          <div className="col" style={{ gap: 10 }}>
            {rows.map((r, i) => {
              const live = backends.data?.find((b) => b.name === r.name);
              const studio = cfg.data?.workspaces.find((w) => w.name === r.name)?.studio_url;
              return (
                <div key={i} className="ws-block">
                  <div className="row" style={{ gap: 8, marginBottom: 8 }}>
                    <label className="row" style={{ gap: 6 }}><input type="radio" checked={!!r.default} onChange={() => set(i, { default: true })} /> <span className="faint">default</span></label>
                    <b className="mono">{r.name || "(new)"}</b>
                    {live && (!live.available
                      ? <Pill tone="bad" title={live.error ?? ""}>unavailable</Pill>
                      : live.verified ? <Pill tone="good">connected</Pill>
                      : <Pill tone="outline" title="Run a sync (or mf doctor --live) to verify access">not checked yet</Pill>)}
                    {live && !live.available && live.error && <span className="faint mono ellipsis" style={{ maxWidth: 520 }} title={live.error}>{live.error}</span>}
                    <span className="spacer" />
                    {studio && <a className="btn sm ghost" href={studio} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Studio</a>}
                    <button className="btn sm ghost" title="Remove" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={13} /></button>
                  </div>
                  {live && <div style={{ marginBottom: 8 }}><ConnectionTest name={r.name} diagnosis={!live.available ? live.diagnosis : null} /></div>}
                  <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
                    <label className="field">Name (run-id prefix)<input className="input mono" value={r.name} onChange={(e) => set(i, { name: slug(e.target.value) })} /></label>
                    <label className="field">Title in dropdown<input className="input" value={r.title ?? ""} onChange={(e) => set(i, { title: e.target.value })} /></label>
                    <label className="field">Subscription ID<input className="input mono" placeholder="00000000-0000-0000-0000-000000000000" value={r.subscription_id} onChange={(e) => set(i, { subscription_id: e.target.value.trim() })} /></label>
                    <label className="field">Resource group<input className="input mono" value={r.resource_group} onChange={(e) => set(i, { resource_group: e.target.value.trim() })} /></label>
                    <label className="field">Workspace<input className="input mono" value={r.workspace} onChange={(e) => set(i, { workspace: e.target.value.trim(), name: r.name || slug(e.target.value) })} /></label>
                  </div>
                </div>
              );
            })}
          </div>
        ) : <div className="muted">No workspaces yet — detect them from your <code>az login</code> or add one manually.</div>}
        <div className="row">
          <button className="btn primary" disabled={save.isPending || rows.some((r) => !r.name || !r.subscription_id || !r.resource_group || !r.workspace)} onClick={() => save.mutate()}>
            {save.isPending ? <Loader2 size={14} className="spin" /> : <Save size={14} />} Save &amp; reconnect
          </button>
          {cfg.data?.source && <Pill tone="outline">loaded from {cfg.data.source}</Pill>}
        </div>
        {save.error && <ErrorBox error={save.error} />}
      </div>
    </Card>
  );
}

/** Pick workspaces from what `az login` can see: a subscription (every one the account has), then its Azure ML
 *  workspaces — no ids to type. Keep as many workspaces as you like; the run list reads one or more of them. */
function WorkspacePicker({ have, onAdd }: { have: AzureWorkspaceCfg[]; onAdd: (w: AzureWorkspaceCfg) => void }) {
  const [sub, setSub] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["azure-available", sub], queryFn: () => api.azureAvailable(sub), retry: false, staleTime: 60_000 });
  const [pick, setPick] = useState("");
  const d = q.data;
  const taken = new Set(have.map((r) => `${r.subscription_id}/${r.resource_group}/${r.workspace}`.toLowerCase()));
  const key = (w: AzureWorkspaceCfg): string => `${w.subscription_id}/${w.resource_group}/${w.workspace}`.toLowerCase();
  const free = (d?.workspaces ?? []).filter((w) => !taken.has(key(w)));
  const names = new Set(have.map((r) => r.name));
  const unique = (n: string): string => { let x = n, i = 2; while (names.has(x)) x = `${n}-${i++}`; return x; };
  return (
    <div className="card" style={{ padding: 12 }} data-testid="workspace-picker">
      {q.isLoading ? <Loading label="Asking az login for your subscriptions and workspaces…" /> : q.error ? <ErrorBox error={q.error} /> : d && (
        <div className="row wrap" style={{ gap: 10, alignItems: "flex-end" }}>
          <label className="field">Subscription
            <select className="select" aria-label="Subscription" value={d.subscription_id} onChange={(e) => { setSub(e.target.value); setPick(""); }} style={{ minWidth: 260 }}>
              {d.subscriptions.map((s) => <option key={s.id} value={s.id}>{s.name}{s.default ? " (active)" : ""}</option>)}
            </select>
          </label>
          <label className="field">Workspace
            <select className="select" aria-label="Workspace" value={pick} onChange={(e) => setPick(e.target.value)} style={{ minWidth: 320 }} disabled={!free.length}>
              <option value="">{free.length ? `choose one of ${free.length}…` : (d.workspaces.length ? "all already added" : "no Azure ML workspace in this subscription")}</option>
              {free.map((w) => <option key={key(w)} value={key(w)}>{w.workspace} · {w.resource_group}{w.location ? ` · ${w.location}` : ""}</option>)}
            </select>
          </label>
          <button className="btn primary" disabled={!pick} onClick={() => {
            const w = free.find((x) => key(x) === pick);
            if (!w) return;
            const { location: _l, ...cfg } = w as AzureWorkspaceCfg & { location?: string | null };
            onAdd({ ...cfg, name: unique(cfg.name) });
            setPick("");
          }}><Plus size={14} /> Add</button>
          <span className="faint" style={{ fontSize: 12 }}>Add as many as you need; the run list's workspace menu reads one or several.</span>
        </div>
      )}
    </div>
  );
}
