import { useSyncExternalStore } from "react";

/** Features that can be switched on and off: a powerful tool for the expert, a light view for a quick look.
 *  Your level (Simple / Standard / Expert, the sidebar switch) sets the defaults; Settings → Features overrides any
 *  of them (per browser). Every feature says what it costs: light = nothing extra is loaded, medium = one more
 *  request when shown, heavy = it reads the run's full metric history or many series. Folding a section (its head)
 *  only hides it for now; switching a feature off removes it and what it loads. */

export type Level = "simple" | "standard" | "expert";
export type Cost = "light" | "medium" | "heavy";
export interface Feature { id: string; area: string; label: string; level: Level; cost: Cost; hint: string }

export const LEVELS: { id: Level; label: string; hint: string }[] = [
  { id: "simple", label: "Simple", hint: "first steps: a run's overview, its curves, configs and logs; the guided pages" },
  { id: "standard", label: "Standard", hint: "the intermediate user: filters, health questions, slices, findings, comparisons with every tab but the expert ones" },
  { id: "expert", label: "Expert", hint: "everything: uncertainty forensics, decoder-layer readouts, all questions, timeline and compute detail" },
];

export const FEATURES: Feature[] = [
  // Metrics view (run and Compare)
  { id: "metrics.sets", area: "Metrics", label: "Metric and loss sets", level: "simple", cost: "light", hint: "one-click groups: all losses, evaluation, per family, per modality, per class" },
  { id: "metrics.filter", area: "Metrics", label: "Class / modality / distance filter", level: "standard", cost: "light", hint: "multi-level filter over the lists, sets and charts" },
  { id: "metrics.questions", area: "Metrics", label: "Training health questions", level: "standard", cost: "heavy", hint: "is the loss diverging, the layer refinement, a task muted … answered from the run's full history" },
  { id: "metrics.drift", area: "Metrics", label: "Loss drift vs the baseline", level: "standard", cost: "heavy", hint: "how each shared loss progresses differently from the baseline, and what the pattern says" },
  { id: "metrics.summary", area: "Metrics", label: "Section / run summary", level: "standard", cost: "light", hint: "start, end, min, max and change per plotted metric in the zoomed section" },
  { id: "metrics.slices", area: "Metrics", label: "Slice breakdowns", level: "standard", cost: "heavy", hint: "a chart per sliced metric family (per class, layer, module …)" },
  { id: "metrics.layers", area: "Metrics", label: "Decoder-layer readout", level: "expert", cost: "light", hint: "final value per decoder layer and the change from the previous layer" },
  { id: "metrics.internals", area: "Metrics", label: "Model internals", level: "expert", cost: "heavy", hint: "cross-attention per decoder layer × modality and query statistics (logged by the instrumentation kit)" },
  { id: "metrics.explain", area: "Metrics", label: "Explain on charts", level: "standard", cost: "medium", hint: "the deterministic reading of a chart (best, plateau, spikes) and Ask AI" },
  // Run page tabs
  { id: "run.review", area: "Run tabs", label: "Expert review", level: "standard", cost: "heavy", hint: "the run checked against the codebase's expectations, training health and the baseline, as one prioritised review" },
  { id: "run.timeline", area: "Run tabs", label: "Timeline", level: "expert", cost: "medium", hint: "stages, events and the command / environment over time" },
  { id: "run.plots", area: "Run tabs", label: "Plots", level: "standard", cost: "medium", hint: "suggested and custom plot specs" },
  { id: "run.uncertainty", area: "Run tabs", label: "Uncertainty", level: "expert", cost: "heavy", hint: "learned task weights: shares over time, balance, neglected heads" },
  { id: "run.setup", area: "Run tabs", label: "Setup", level: "standard", cost: "medium", hint: "the training setup read from configs, metrics and compute" },
  { id: "run.findings", area: "Run tabs", label: "Findings", level: "standard", cost: "heavy", hint: "the deterministic analyses (pitfalls, dynamics, sparse detection)" },
  { id: "run.precision", area: "Run tabs", label: "Precision & recall", level: "standard", cost: "medium", hint: "per class and range from the evaluation files" },
  { id: "run.sequences", area: "Run tabs", label: "Sequences", level: "standard", cost: "heavy", hint: "per-sequence evaluation parquets" },
  { id: "run.dataset", area: "Run tabs", label: "Dataset", level: "standard", cost: "light", hint: "datasets the run read" },
  { id: "run.compute", area: "Run tabs", label: "Compute", level: "expert", cost: "medium", hint: "GPU, memory and throughput" },
  { id: "run.ai", area: "Run tabs", label: "AI", level: "standard", cost: "light", hint: "grounded questions about the run (needs an LLM)" },
  // Compare tabs
  { id: "compare.uncertainty", area: "Compare tabs", label: "Uncertainty", level: "expert", cost: "heavy", hint: "the candidate studied against the baseline" },
  { id: "compare.plots", area: "Compare tabs", label: "Plots", level: "standard", cost: "medium", hint: "plots of the candidate" },
  { id: "compare.setup", area: "Compare tabs", label: "Setup", level: "standard", cost: "medium", hint: "all subjects side by side" },
  { id: "compare.findings", area: "Compare tabs", label: "Findings", level: "standard", cost: "heavy", hint: "analyses of the candidate against the baseline" },
];

const RANK: Record<Level, number> = { simple: 0, standard: 1, expert: 2 };
const MODE_KEY = "mf.mode";
const OVR_KEY = "mf.features";

function read<T>(key: string, fallback: T): T {
  try { const raw = localStorage.getItem(key); return raw === null ? fallback : (JSON.parse(raw) as T); } catch { return fallback; }
}
function write(key: string, v: unknown): void {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* storage unavailable */ }
}

interface State { level: Level; overrides: Record<string, boolean> }
let state: State = { level: read<Level>(MODE_KEY, "standard"), overrides: read<Record<string, boolean>>(OVR_KEY, {}) };
const listeners = new Set<() => void>();
const emit = (): void => listeners.forEach((l) => l());
const subscribe = (l: () => void): (() => void) => { listeners.add(l); return () => listeners.delete(l); };
const snapshot = (): State => state;

export function setLevel(level: Level): void { state = { ...state, level }; write(MODE_KEY, level); emit(); }
export function setFeature(id: string, on: boolean | null): void {
  const overrides = { ...state.overrides };
  if (on === null) delete overrides[id]; else overrides[id] = on;
  state = { ...state, overrides }; write(OVR_KEY, overrides); emit();
}
/** Presets: "light" switches every medium / heavy feature off; "full" switches everything on; "level" = defaults. */
export function applyPreset(p: "light" | "full" | "level"): void {
  const overrides = p === "level" ? {} : Object.fromEntries(FEATURES.map((f) => [f.id, p === "full" ? true : f.cost === "light" && RANK[f.level] <= RANK[state.level]]));
  state = { ...state, overrides }; write(OVR_KEY, overrides); emit();
}

export const byLevel = (f: Feature, level: Level): boolean => RANK[level] >= RANK[f.level];
export function isOn(id: string, s: State = state): boolean {
  const f = FEATURES.find((x) => x.id === id);
  if (!f) return true;
  return s.overrides[id] ?? byLevel(f, s.level);
}

export function useFeatures(): State & { on: (id: string) => boolean } {
  const s = useSyncExternalStore(subscribe, snapshot);
  return { ...s, on: (id: string) => isOn(id, s) };
}
export const useFeature = (id: string): boolean => useFeatures().on(id);
export const useLevel = (): [Level, (l: Level) => void] => [useFeatures().level, setLevel];
