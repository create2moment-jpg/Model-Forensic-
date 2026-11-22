import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserRound, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { Card, ErrorBox } from "./ui";

/** Who "me" is in runs: an explicit list of names (exact, case-insensitive) — used by Mine, My experiments,
 *  `user:me`. Add or remove names here or with *Add to Me* on a run. */
export function MeSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["me-names"], queryFn: api.meNames });
  const [name, setName] = useState("");
  const save = useMutation({ mutationFn: api.setMeNames, onSuccess: (r) => { qc.setQueryData(["me-names"], r); setName(""); void qc.invalidateQueries({ queryKey: ["runs"] }); void qc.invalidateQueries({ queryKey: ["experiments"] }); } });
  const names = q.data?.names ?? [];
  return (
    <Card title={<span className="row"><UserRound size={15} /> Who is &ldquo;me&rdquo;</span>} sub={q.data ? `from ${q.data.source} · exact names only (case-insensitive), never guessed from other text` : undefined}>
      <div className="row wrap" style={{ gap: 6 }}>
        {names.map((n) => (
          <span key={n} className="tag row" style={{ gap: 4 }}>{n}
            {q.data?.editable && <X size={11} style={{ cursor: "pointer" }} aria-label={`remove ${n}`} onClick={() => save.mutate(names.filter((x) => x !== n))} />}
          </span>
        ))}
        {names.length === 0 && <span className="muted">No names yet — add how you appear in runs (display name, e-mail, user name).</span>}
      </div>
      {q.data?.editable && (
        <form className="row" style={{ gap: 6, marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); if (name.trim()) save.mutate([...names, name.trim()]); }}>
          <input className="input" aria-label="Add a name" placeholder="e.g. your display name or e-mail" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 300 }} />
          <button className="btn sm" type="submit" disabled={!name.trim() || save.isPending}>Add</button>
        </form>
      )}
      {save.error && <ErrorBox error={save.error} />}
    </Card>
  );
}

/** *Add to Me* next to a run's owner when that name is not one of yours yet. */
export function AddToMe({ user }: { user: string | null | undefined }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["me-names"], queryFn: api.meNames, staleTime: 60_000 });
  const add = useMutation({ mutationFn: (n: string) => api.setMeNames([...(q.data?.names ?? []), n]), onSuccess: (r) => { qc.setQueryData(["me-names"], r); void qc.invalidateQueries({ queryKey: ["runs"] }); } });
  if (!user || !q.data?.editable) return null;
  const mine = q.data.names.some((n) => n.toLowerCase() === user.toLowerCase());
  if (mine) return <span className="tag" title="This owner is one of your names">me</span>;
  return <button className="btn ghost sm" title={`Treat “${user}” as you (Mine, My experiments, user:me)`} disabled={add.isPending} onClick={() => add.mutate(user)}>Add to Me</button>;
}
