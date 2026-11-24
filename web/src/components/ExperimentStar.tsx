import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Star } from "lucide-react";
import { api } from "../api/client";

/** Star / unstar an experiment (by name): starred experiments come first everywhere and filter with ``exp:starred``. */
export function ExperimentStar({ name, starred, size = 13 }: { name: string; starred: boolean; size?: number }) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => api.starExperiment(name, !starred),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["experiments"] }); void qc.invalidateQueries({ queryKey: ["runs"] }); },
  });
  return (
    <button type="button" className={`star-btn${starred ? " on" : ""}`} disabled={m.isPending} aria-pressed={starred}
      aria-label={`${starred ? "Unstar" : "Star"} experiment ${name}`} title={starred ? "Starred experiment — click to unstar" : "Star this experiment"}
      onClick={(e) => { e.stopPropagation(); m.mutate(); }} data-testid="experiment-star">
      <Star size={size} fill={starred ? "currentColor" : "none"} />
    </button>
  );
}
