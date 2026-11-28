import { useQuery } from "@tanstack/react-query";
import { ArrowDownToLine, ChevronDown, ChevronUp, Download, FileText, RefreshCw, Search, Star } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { LogFileInfo } from "../../api/types";
import { fmtBytes } from "../../lib/format";
import { useLocalStorage } from "../../lib/hooks";
import { Card, Empty, ErrorBox, Loading, Switch } from "../../components/ui";

const ERR = /(error|exception|traceback|fatal|nan\b|out of memory|killed|segfault)/i;
const WARN = /(warn|deprecat|retry|timeout)/i;
const MAX_LINES = 50_000;

/** How much of a log is read: its end (a ranged read — a large log is not downloaded to show its last lines). */
const TAILS: [number, string][] = [[400_000, "last 400 KB"], [4_000_000, "last 4 MB"], [40_000_000, "last 40 MB"], [0, "whole file"]];

/** A run's logs. Every log of the run and its pipeline stages is listed (``stage``: that stage's), the most telling
 *  one first — the training process's std_log, a failed stage's before the others, never a pipeline's orchestration
 *  log or a platform log first — and any other can be picked, read further back, or downloaded whole. */
/** ``at``: open this file at this line (from Log insights): the line number when the whole file is shown, else the
 *  line found by its text (the view shows the log's end, numbered from there). */
export function LogsTab({ runId, stage, at }: { runId: string; stage?: string; at?: { file: string; line: number; text: string } }) {
  const files = useQuery({ queryKey: ["log-files", runId, stage ?? ""], queryFn: () => api.logFiles(runId, stage) });
  const [pick, setPick] = useState<string | null>(at?.file ?? null);
  const [tail, setTail] = useLocalStorage("mf.logs.tail", 400_000);
  const list = files.data?.files ?? [];
  const id = pick && list.some((f) => f.id === pick) ? pick : files.data?.recommended ?? null;
  const file = list.find((f) => f.id === id);
  const logs = useQuery({ queryKey: ["log-file", runId, id, tail], queryFn: () => api.logFile(runId, id!, tail), enabled: !!id });
  const text = logs.data?.text;
  const [q, setQ] = useState("");
  const [onlyMatches, setOnlyMatches] = useState(false);
  const [cursor, setCursor] = useState(0);
  // wrap long lines at word boundaries (continuation indented), or keep one line per line and scroll sideways
  const [wrap, setWrap] = useLocalStorage("mf.logs.wrap", true);
  const box = useRef<HTMLDivElement>(null);

  const lines = useMemo(() => (text ?? "").split("\n").slice(-MAX_LINES), [text]);
  const rx = useMemo(() => { try { return q ? new RegExp(q, "gi") : null; } catch { return null; } }, [q]);
  const matches = useMemo(() => (rx ? lines.map((l, i) => (l.match(rx) ? i : -1)).filter((i) => i >= 0) : []), [lines, rx]);
  const errors = useMemo(() => lines.map((l, i) => (ERR.test(l) ? i : -1)).filter((i) => i >= 0), [lines]);
  const shown = onlyMatches && rx ? matches : lines.map((_, i) => i);
  // the line asked for: by number when the file is shown whole, else by its text (the end of a log is numbered from there)
  const target = useMemo(() => {
    if (!at || !logs.data || at.file !== id) return -1;
    const want = at.text.trim().slice(0, 60);
    if (!logs.data.partial && at.line > 0 && at.line <= lines.length && (!want || lines[at.line - 1]!.includes(want))) return at.line - 1;
    return want ? lines.findIndex((l) => l.includes(want)) : -1;
  }, [at, logs.data, id, lines]);
  useEffect(() => {
    if (target < 0) return;
    const t = setTimeout(() => box.current?.querySelector(`[data-ln="${target}"]`)?.scrollIntoView({ block: "center" }), 50);
    return () => clearTimeout(t);
  }, [target]);

  const jump = (lineIdx: number | undefined): void => {
    if (lineIdx === undefined) return;
    box.current?.querySelector(`[data-ln="${lineIdx}"]`)?.scrollIntoView({ block: "center" });
  };
  const step = (d: number): void => { const n = (cursor + d + matches.length) % Math.max(1, matches.length); setCursor(n); jump(matches[n]); };
  if (files.isLoading) return <Loading label="Listing the logs…" />;
  if (files.error) return <ErrorBox error={files.error} />;
  if (!list.length) return <Card><Empty title={stage ? `No logs for stage ${stage}` : "No logs for this run"} /></Card>;
  const short = !!logs.data && !logs.data.partial && lines.filter((l) => l.trim()).length < 15;
  const others = list.filter((f) => f.id !== id).slice(0, 4);

  return (
    <div className="col" style={{ gap: 10 }}>
    <LogPicker files={list} value={id} recommended={files.data?.recommended ?? null} onChange={setPick} tail={tail} onTail={setTail} runId={runId} />
    {logs.isLoading ? <Loading label={`Reading ${file?.path ?? "the log"} (${TAILS.find(([n]) => n === tail)?.[1] ?? "its end"})…`} /> : logs.error ? <ErrorBox error={logs.error} /> : (
    <Card flush title={<span className="row mono" style={{ gap: 6, minWidth: 0 }}><FileText size={14} /><span className="trunc" title={file?.path}>{file?.stage ? `${file.stage} / ` : ""}{file?.path}</span></span>}
      right={<>
      <div className="input-wrap" style={{ width: 280 }}><Search size={13} /><input className="input mono" placeholder="regex, e.g. loss=nan|CUDA" value={q} onChange={(e) => { setQ(e.target.value); setCursor(0); }} onKeyDown={(e) => e.key === "Enter" && step(e.shiftKey ? -1 : 1)} /></div>
      {rx && <span className="faint num">{matches.length ? `${cursor + 1}/${matches.length}` : "0"}</span>}
      <button className="btn sm icon" onClick={() => step(-1)} disabled={!matches.length}><ChevronUp size={13} /></button>
      <button className="btn sm icon" onClick={() => step(1)} disabled={!matches.length}><ChevronDown size={13} /></button>
      <span className="row muted">Matches only <Switch on={onlyMatches} onChange={setOnlyMatches} label="Matches only" /></span>
      <span className="row muted" title="Wrap long lines at word boundaries (off: one line per log line, scroll sideways)">Wrap lines <Switch on={wrap} onChange={setWrap} label="Wrap lines" /></span>
      <button className="btn sm" disabled={!errors.length} onClick={() => jump(errors[0])}>First error</button>
      <button className="btn sm icon" title="Jump to end" onClick={() => jump(lines.length - 1)}><ArrowDownToLine size={13} /></button>
      <button className="btn sm icon" title="Reload" onClick={() => void logs.refetch()}><RefreshCw size={13} /></button>
    </>}>
      <div className="faint" style={{ padding: "6px 12px", fontSize: 12, borderBottom: "1px solid var(--line)" }} data-testid="log-counts">{lines.length.toLocaleString()} lines · {errors.length} error-like{logs.data?.size_bytes != null ? ` · ${fmtBytes(logs.data.size_bytes)}` : ""}</div>
      {logs.data?.partial && <div className="note" style={{ margin: "8px 12px" }} data-testid="log-partial">Showing the end of the log ({fmtBytes(logs.data.read_bytes)}{logs.data.size_bytes ? ` of ${fmtBytes(logs.data.size_bytes)}` : ""}). <button className="btn sm" onClick={() => setTail(TAILS[TAILS.findIndex(([n]) => n === tail) + 1]?.[0] ?? 0)}>Read further back</button></div>}
      {short && others.length > 0 && <div className="note warn" style={{ margin: "8px 12px" }} data-testid="log-short">This log says little ({lines.filter((l) => l.trim()).length} lines). Other logs: {others.map((f) => <button key={f.id} className="btn sm" style={{ marginLeft: 6 }} onClick={() => setPick(f.id)}>{f.stage ? `${f.stage} / ` : ""}{f.path}</button>)}</div>}
      <div className={`log ${wrap ? "wrap" : "nowrap"}`} ref={box} data-testid="log-view" data-wrap={wrap ? "on" : "off"}>
        {shown.map((i) => {
          const l = lines[i] ?? "";
          const cls = ERR.test(l) ? "err" : WARN.test(l) ? "warn" : "";
          return (
            <div key={i} data-ln={i} className={`log-line ${cls}${i === target ? " target" : ""}`} data-target={i === target || undefined}>
              <span className="ln">{i + 1}</span>
              <span>{rx ? highlight(l, rx) : l}</span>
            </div>
          );
        })}
      </div>
      <div className="faint" style={{ padding: "6px 12px", fontSize: 11.5, borderTop: "1px solid var(--line)" }}>Log content is untrusted input: it is shown verbatim here and fenced as data when given to the AI.</div>
    </Card>
    )}
    </div>
  );
}

/** Which log, and how much of it: every log of the run (by stage), the recommended one starred. */
function LogPicker({ files, value, recommended, onChange, tail, onTail, runId }: {
  files: LogFileInfo[]; value: string | null; recommended: string | null; onChange: (id: string) => void; tail: number; onTail: (n: number) => void; runId: string;
}) {
  const groups = useMemo(() => {
    const m = new Map<string, LogFileInfo[]>();
    files.forEach((f) => m.set(f.stage ?? "", [...(m.get(f.stage ?? "") ?? []), f]));
    return [...m.entries()];
  }, [files]);
  const cur = files.find((f) => f.id === value);
  const label = (f: LogFileInfo): string => `${f.id === recommended ? "★ " : ""}${f.path}${f.size_bytes != null ? ` · ${fmtBytes(f.size_bytes)}` : ""} — ${f.label}`;
  return (
    <div className="card card-body row log-picker" style={{ gap: 10, flexWrap: "wrap" }} data-testid="log-picker">
      <span className="faint">Log</span>
      <select className="input mono" style={{ flex: "1 1 360px", minWidth: 0 }} value={value ?? ""} onChange={(e) => onChange(e.target.value)} aria-label="Log file" data-testid="log-file-select">
        {groups.map(([st, fs]) => st
          ? <optgroup key={st} label={`stage ${st}${fs.some((f) => f.failed_stage) ? " (failed)" : ""}`}>{fs.map((f) => <option key={f.id} value={f.id}>{label(f)}</option>)}</optgroup>
          : <optgroup key="_own" label={groups.length > 1 ? "the pipeline itself (orchestration)" : "this run"}>{fs.map((f) => <option key={f.id} value={f.id}>{label(f)}</option>)}</optgroup>)}
      </select>
      <span className="faint">{files.length} log{files.length === 1 ? "" : "s"}</span>
      {cur && cur.id === recommended && <span className="row faint" style={{ gap: 4 }} title="Chosen first: the training process's output (rank 0), a failed stage's before the others; platform and orchestration logs last"><Star size={12} /> most telling</span>}
      <span className="row" style={{ gap: 6, marginLeft: "auto" }}>
        <span className="faint">Read</span>
        <select className="input" value={tail} onChange={(e) => onTail(Number(e.target.value))} aria-label="How much to read" data-testid="log-tail">
          {TAILS.map(([n, l]) => <option key={n} value={n}>{l}</option>)}
        </select>
        {value && <a className="btn sm" href={api.artifactUrl(runId, value)} download title={`The whole file, as stored${cur?.size_bytes ? ` (${fmtBytes(cur.size_bytes)})` : ""}`} data-testid="log-download"><Download size={13} /> Download</a>}
      </span>
    </div>
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
