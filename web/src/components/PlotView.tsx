import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PlotSpecV1 } from "../api/types";
import { Chart, PALETTE } from "./Chart";
import { ErrorBox, Loading, Pill } from "./ui";

/** Draws one validated plot spec (the server validates and resolves it; an invalid spec shows its errors). */
export function PlotView({ spec, height = 220, baseline }: { spec: PlotSpecV1; height?: number; baseline?: string | null }) {
  const q = useQuery({ queryKey: ["plot", JSON.stringify(spec)], queryFn: () => api.plotRender(spec), staleTime: 60_000 });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  // one colour per metric, the baseline dashed: baseline and candidate of one metric overlap in the same colour
  const keys = [...new Set(d.series.map((s) => s.key))];
  const runsIn = [...new Set(d.series.map((s) => s.run))];
  return (
    <div className="card" data-testid="plot-view" data-plot={spec.id}>
      <div className="card-head">
        {spec.source && <Pill tone="outline">{spec.source}</Pill>}
        {spec.question && <span className="faint" style={{ fontSize: 12 }}>{spec.question}</span>}
      </div>
      <div className="card-body">
        <div className="faint mono" style={{ fontSize: 11, marginBottom: 4 }} data-testid="plot-what">
          {keys.slice(0, 6).join(" · ")}{keys.length > 6 ? ` · +${keys.length - 6}` : ""}{runsIn.length > 1 ? ` — ${runsIn.length} runs${baseline && runsIn.includes(baseline) ? " (baseline dashed)" : ""}` : ""}
        </div>
        <Chart title={spec.title} series={d.series.map((s) => ({ name: s.label, x: s.x, y: s.y, color: PALETTE[keys.indexOf(s.key) % PALETTE.length], dash: !!baseline && s.run === baseline }))} height={height} logY={spec.y?.scale === "log"} xLabel={spec.x?.label || "step"} />
        {d.warnings.map((w) => <div key={w} className="faint" style={{ fontSize: 12 }}>⚠ {w}</div>)}
      </div>
    </div>
  );
}
