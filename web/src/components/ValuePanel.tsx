import { useQuery } from "@tanstack/react-query";
import { Hash } from "lucide-react";
import { useMemo } from "react";
import { api } from "../api/client";
import type { MetricSeriesDto } from "../api/types";
import { fmtNum } from "../lib/format";
import { PALETTE } from "./Chart";
import { Card, Loading } from "./ui";

export interface ValueEntry { key: string; metric: string; stage: string | null; points?: number }

/** A metric logged once (or twice) per stage — an evaluation stage's result — is a value, not a curve: a line chart of
 *  one point says nothing. Shown here instead as bars: per metric, every stage that logged it (pipeline order) and
 *  every run, with the change from the previous stage and against the baseline. */
export function ValuePanel({ runId, runs, baseline, entries, keys, stages }: {
  runId: string; runs: string[]; baseline: string | null; entries: ValueEntry[]; keys: string[]; stages: string[];
}) {
  const metrics = useMemo(() => [...new Set(keys.map((k) => entries.find((e) => e.key === k)?.metric ?? k))], [keys, entries]);
  // every stage's copy of each picked metric (the main run's catalogue), plus the plain name for the other runs
  const fetchKeys = useMemo(() => [...new Set(metrics.flatMap((m) => [...entries.filter((e) => e.metric === m && (e.points ?? 99) <= 2).map((e) => e.key), m]))], [metrics, entries]);
  const q = useQuery({
    queryKey: ["values", runs, fetchKeys],
    queryFn: async () => Object.fromEntries(await Promise.all(runs.map(async (r) => [r, await api.metrics(r, fetchKeys, 10)] as const))),
    enabled: fetchKeys.length > 0,
  });
  const order = (st: string): number => (st === "final" ? 1e6 : stages.indexOf(st) >= 0 ? stages.indexOf(st) : 1e5);
  const short = (r: string): string => r.split(":").pop()!.split("+").pop()!;
  const blocks = useMemo(() => metrics.map((m) => {
    const rows: { run: string; stage: string; value: number }[] = [];
    for (const r of runs) {
      const got = (q.data?.[r] ?? []) as MetricSeriesDto[];
      const staged = got.filter((s) => s.key !== m);  // the stage copies (the plain name is the headline)
      const use = staged.length ? staged : got.filter((s) => s.key === m);
      for (const s of use) {
        const v = [...s.values].reverse().find((x) => x !== null && Number.isFinite(x));
        if (v === undefined || v === null) continue;
        const st = entries.find((e) => e.key === s.key)?.stage ?? stageOf(s.key, m) ?? "final";
        if (!rows.some((x) => x.run === r && x.stage === st)) rows.push({ run: r, stage: st, value: v });
      }
    }
    rows.sort((a, b) => order(a.stage) - order(b.stage) || runs.indexOf(a.run) - runs.indexOf(b.run));
    const top = Math.max(...rows.map((x) => Math.abs(x.value)), 1e-12);
    return { m, rows, top };
  }), [metrics, runs, q.data, entries, stages]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Card title={<span className="row"><Hash size={15} /> Values per stage</span>} fold="metrics.values"
      sub="logged once per stage (evaluation) — a value, not a curve: compared across stages and runs">
      {q.isLoading ? <Loading /> : (
        <div className="col" style={{ gap: 14 }} data-testid="value-panel">
          {blocks.map(({ m, rows, top }) => (
            <div key={m} data-metric={m}>
              <div className="mono" style={{ fontSize: 12.5, marginBottom: 4 }}>{m}</div>
              {rows.length === 0 && <div className="faint">no value logged</div>}
              <table className="t compact value-bars" data-nosort>
                <tbody>
                  {rows.map((x) => {
                    const prev = rows.filter((y) => y.run === x.run && order(y.stage) < order(x.stage)).pop();
                    const base = baseline && x.run !== baseline ? rows.find((y) => y.run === baseline && y.stage === x.stage) ?? rows.find((y) => y.run === baseline && y.stage === "final") : undefined;
                    const rel = (a: number, b: number): string => { const d = (a - b) / (Math.abs(b) || 1e-12); return `${d >= 0 ? "+" : ""}${(d * 100).toFixed(1)}%`; };
                    const i = runs.indexOf(x.run);
                    return (
                      <tr key={`${x.run}|${x.stage}`} data-stage={x.stage} data-run={x.run}>
                        <td className="nowrap" style={{ width: 200 }}>{x.stage}</td>
                        <td className="mono nowrap faint trunc" style={{ width: 170, maxWidth: 170 }} title={x.run}>{x.run === runId ? short(x.run) : `${short(x.run)}${x.run === baseline ? " (baseline)" : ""}`}</td>
                        <td style={{ minWidth: 120 }}>
                          <div className="value-bar" style={{ width: `${Math.max(2, (Math.abs(x.value) / top) * 100)}%`, background: PALETTE[i % PALETTE.length], opacity: x.run === baseline ? 0.55 : 0.9 }} />
                        </td>
                        <td className="r num" style={{ width: 90 }}>{fmtNum(x.value)}</td>
                        <td className="r num faint" style={{ width: 150 }}>{prev ? `${rel(x.value, prev.value)} vs ${prev.stage}` : ""}</td>
                        <td className="r num faint" style={{ width: 120 }}>{base ? `${rel(x.value, base.value)} vs baseline` : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/** ``val/NDS[stage=evaluate_finetune]`` → ``evaluate_finetune`` when the key is ``metric`` with a stage dimension. */
function stageOf(key: string, metric: string): string | null {
  const m = /^(.*?)\[(.*)\]$/.exec(key);
  if (!m) return null;
  const dims = m[2]!.split(",").map((p) => p.split("=") as [string, string]);
  const st = dims.find(([k]) => k === "stage")?.[1];
  if (!st) return null;
  const rest = dims.filter(([k]) => k !== "stage").map(([k, v]) => `${k}=${v}`).join(",");
  return (rest ? `${m[1]}[${rest}]` : m[1]) === metric ? st : null;
}
