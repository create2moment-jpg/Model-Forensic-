/** A run's tags, quietly: ``role=baseline`` / ``role=candidate`` get a badge (they decide what is compared with what),
 *  everything else is small grey text — tags are context, not status. */
import type { Run } from "../api/types";

const ROLE_BADGE: Record<string, string> = { baseline: "badge-baseline", candidate: "badge-candidate" };

export function RunTags({ run, workspace = false, max = 3 }: { run: Run; workspace?: boolean; max?: number }) {
  const tags = Object.entries(run.tags);
  const role = run.tags.role;
  const badge = role ? ROLE_BADGE[role] : undefined;
  const quiet = tags.filter(([k]) => !(k === "role" && badge)).slice(0, max);
  if (!workspace && !badge && !quiet.length) return null;
  return (
    <div className="run-tags" data-testid="run-tags">
      {workspace && <span className="tag" style={{ background: "var(--accent-bg)", color: "var(--accent-2)" }} title="Workspace">{run.ref.backend}</span>}
      {badge && <span className={`tag ${badge}`} title={`tag role=${role}`}>{role}</span>}
      {quiet.length > 0 && <span className="tag-quiet" title={tags.map(([k, v]) => `${k}=${v}`).join("\n")}>{quiet.map(([k, v]) => `${k}=${v}`).join(" · ")}</span>}
    </div>
  );
}
