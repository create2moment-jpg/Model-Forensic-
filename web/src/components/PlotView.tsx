import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PlotSpecV1 } from "../api/types";
import { Chart } from "./Chart";
import { ErrorBox, Loading, Pill } from "./ui";

/** Draws one validated plot spec (the server validates and resolves it; an invalid spec shows its errors). */
export function PlotView({ spec, height = 220 }: { spec: PlotSpecV1; height?: number }) {
  const q = useQuery({ queryKey: ["plot", JSON.stringify(spec)], queryFn: () => api.plotRender(spec), staleTime: 60_000 });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  return (
    <div className="card" data-testid="plot-view" data-plot={spec.id}>
      <div className="card-head">
        <h3>{spec.title}</h3>
        {spec.source && <Pill tone="outline">{spec.source}</Pill>}
        {spec.question && <span className="faint" style={{ fontSize: 12 }}>{spec.question}</span>}
      </div>
      <div className="card-body">
        <Chart series={d.series.map((s) => ({ name: s.label, x: s.x, y: s.y }))} height={height} logY={spec.y?.scale === "log"} xLabel={spec.x?.label || "step"} />
        {d.warnings.map((w) => <div key={w} className="faint" style={{ fontSize: 12 }}>⚠ {w}</div>)}
      </div>
    </div>
  );
}
