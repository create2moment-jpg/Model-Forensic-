import { useQuery } from "@tanstack/react-query";
import { ArrowDownToLine, ChevronDown, ChevronUp, Download, RefreshCw, Search } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { Card, Empty, ErrorBox, Loading, Switch } from "../../components/ui";

const ERR = /(error|exception|traceback|fatal|nan\b|out of memory|killed|segfault)/i;
const WARN = /(warn|deprecat|retry|timeout)/i;
const MAX_LINES = 50_000;

export function LogsTab({ runId }: { runId: string }) {
  const logs = useQuery({ queryKey: ["logs", runId], queryFn: () => api.logs(runId) });
  const [q, setQ] = useState("");
  const [onlyMatches, setOnlyMatches] = useState(false);
  const [cursor, setCursor] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  const lines = useMemo(() => (logs.data ?? "").split("\n").slice(-MAX_LINES), [logs.data]);
  const rx = useMemo(() => { try { return q ? new RegExp(q, "gi") : null; } catch { return null; } }, [q]);
  const matches = useMemo(() => (rx ? lines.map((l, i) => (l.match(rx) ? i : -1)).filter((i) => i >= 0) : []), [lines, rx]);
  const errors = useMemo(() => lines.map((l, i) => (ERR.test(l) ? i : -1)).filter((i) => i >= 0), [lines]);
  const shown = onlyMatches && rx ? matches : lines.map((_, i) => i);

  const jump = (lineIdx: number | undefined): void => {
    if (lineIdx === undefined) return;
    box.current?.querySelector(`[data-ln="${lineIdx}"]`)?.scrollIntoView({ block: "center" });
  };
  const step = (d: number): void => { const n = (cursor + d + matches.length) % Math.max(1, matches.length); setCursor(n); jump(matches[n]); };
  const download = (): void => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([logs.data ?? ""], { type: "text/plain" }));
    a.download = `${runId.replace(/[:/]/g, "_")}.log`;
    a.click();
  };

  if (logs.isLoading) return <Loading label="Fetching logs…" />;
  if (logs.error) return <ErrorBox error={logs.error} />;
  if (!logs.data) return <Card><Empty title="No logs for this run" /></Card>;

  return (
    <Card flush title="Logs" sub={`${lines.length.toLocaleString()} lines · ${errors.length} error-like`} right={<>
      <div className="input-wrap" style={{ width: 280 }}><Search size={13} /><input className="input mono" placeholder="regex, e.g. loss=nan|CUDA" value={q} onChange={(e) => { setQ(e.target.value); setCursor(0); }} onKeyDown={(e) => e.key === "Enter" && step(e.shiftKey ? -1 : 1)} /></div>
      {rx && <span className="faint num">{matches.length ? `${cursor + 1}/${matches.length}` : "0"}</span>}
      <button className="btn sm icon" onClick={() => step(-1)} disabled={!matches.length}><ChevronUp size={13} /></button>
      <button className="btn sm icon" onClick={() => step(1)} disabled={!matches.length}><ChevronDown size={13} /></button>
      <span className="row muted">Matches only <Switch on={onlyMatches} onChange={setOnlyMatches} /></span>
      <button className="btn sm" disabled={!errors.length} onClick={() => jump(errors[0])}>First error</button>
      <button className="btn sm icon" title="Jump to end" onClick={() => jump(lines.length - 1)}><ArrowDownToLine size={13} /></button>
      <button className="btn sm icon" title="Reload" onClick={() => void logs.refetch()}><RefreshCw size={13} /></button>
      <button className="btn sm icon" title="Download" onClick={download}><Download size={13} /></button>
    </>}>
      <div className="log" ref={box}>
        {shown.map((i) => {
          const l = lines[i] ?? "";
          const cls = ERR.test(l) ? "err" : WARN.test(l) ? "warn" : "";
          return (
            <div key={i} data-ln={i} className={`log-line ${cls}`}>
              <span className="ln">{i + 1}</span>
              <span>{rx ? highlight(l, rx) : l}</span>
            </div>
          );
        })}
      </div>
      <div className="faint" style={{ padding: "6px 12px", fontSize: 11.5, borderTop: "1px solid var(--line)" }}>Log content is untrusted input: it is shown verbatim here and fenced as data when given to the AI.</div>
    </Card>
  );
}

function highlight(line: string, rx: RegExp): (string | JSX.Element)[] {
  const out: (string | JSX.Element)[] = [];
  let last = 0;
  for (const m of line.matchAll(rx)) {
    if (m.index === undefined || m[0] === "") break;
    out.push(line.slice(last, m.index), <mark key={m.index}>{m[0]}</mark>);
    last = m.index + m[0].length;
  }
  out.push(line.slice(last));
  return out;
}
