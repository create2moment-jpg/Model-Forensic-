import { SlidersHorizontal } from "lucide-react";
import { FEATURES, LEVELS, applyPreset, byLevel, setFeature, setLevel, useFeatures } from "../lib/features";
import { Card, Pill, Switch } from "./ui";

const COST_TONE = { light: "good", medium: "outline", heavy: "warn" } as const;

/** Settings → Features: the user level (defaults), presets (light view / everything) and every feature on or off. */
export function FeatureSettings() {
  const f = useFeatures();
  const areas = [...new Set(FEATURES.map((x) => x.area))];
  const changed = Object.keys(f.overrides).length;
  const heavyOn = FEATURES.filter((x) => x.cost === "heavy" && f.on(x.id)).length;
  return (
    <Card title={<span className="row"><SlidersHorizontal size={15} /> Features</span>} fold="settings.features"
      sub={`${FEATURES.filter((x) => f.on(x.id)).length} of ${FEATURES.length} on · ${heavyOn} heavy · ${changed ? `${changed} changed from the level` : "level defaults"}`}>
      <div data-testid="feature-settings" className="col" style={{ gap: 12 }}>
        <div className="row wrap" style={{ gap: 8 }}>
          <span className="faint" style={{ width: 70 }}>Level</span>
          {LEVELS.map((l) => (
            <button key={l.id} type="button" className={`btn sm ${f.level === l.id ? "primary" : ""}`} title={l.hint} aria-pressed={f.level === l.id} onClick={() => setLevel(l.id)}>{l.label}</button>
          ))}
          <span className="faint" style={{ fontSize: 12 }}>{LEVELS.find((l) => l.id === f.level)?.hint}</span>
        </div>
        <div className="row wrap" style={{ gap: 8 }}>
          <span className="faint" style={{ width: 70 }}>Presets</span>
          <button type="button" className="btn sm" onClick={() => applyPreset("light")} title="Only features that load nothing extra (within your level)">Light view</button>
          <button type="button" className="btn sm" onClick={() => applyPreset("level")} title="Forget single changes: your level decides">Level defaults</button>
          <button type="button" className="btn sm" onClick={() => applyPreset("full")} title="Every feature, whatever the level">Everything</button>
          <span className="faint" style={{ fontSize: 12 }}>Folding a section only hides it; a feature switched off is gone and loads nothing.</span>
        </div>
        {areas.map((a) => (
          <div key={a}>
            <div className="sub-h">{a}</div>
            <table className="t compact" data-nosort style={{ tableLayout: "fixed", width: "100%" }}>
              <tbody>
                {FEATURES.filter((x) => x.area === a).map((x) => {
                  const ovr = f.overrides[x.id];
                  return (
                    <tr key={x.id} data-feature={x.id}>
                      <td style={{ width: 44 }}><Switch label={x.label} on={f.on(x.id)} onChange={(v) => setFeature(x.id, v === byLevel(x, f.level) ? null : v)} /></td>
                      <td style={{ width: 240 }}><b>{x.label}</b></td>
                      <td className="faint trunc" title={x.hint}>{x.hint}</td>
                      <td style={{ width: 90 }}><Pill tone="outline">{x.level}</Pill></td>
                      <td style={{ width: 80 }}><Pill tone={COST_TONE[x.cost]}>{x.cost}</Pill></td>
                      <td style={{ width: 70 }}>{ovr !== undefined && <button type="button" className="btn ghost sm" title="Back to the level's default" onClick={() => setFeature(x.id, null)}>reset</button>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </Card>
  );
}
