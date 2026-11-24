import { Palette, Sparkles, Wind } from "lucide-react";
import { THEMES, useAppearance } from "../lib/appearance";
import { Card, Switch } from "./ui";

/** Settings → Appearance: the theme, expressive touches and motion — how it looks, nothing else changes. */
export function AppearanceSettings() {
  const [a, setA] = useAppearance();
  return (
    <Card title={<span className="row"><Palette size={15} /> Appearance</span>} sub="Remembered in this browser">
      <div className="col" style={{ gap: 14 }} data-testid="appearance-settings">
        <div className="theme-grid" role="radiogroup" aria-label="Theme">
          {THEMES.map((t) => (
            <button key={t.id} type="button" className="theme-tile" aria-pressed={a.theme === t.id} role="radio" aria-checked={a.theme === t.id}
              onClick={() => { setA({ ...a, theme: t.id }); if (t.mode === "dark") { try { localStorage.setItem("mf.appearance.dark", t.id); } catch { /* fine */ } } }}
              data-theme-id={t.id} title={t.note}>
              <span className="theme-swatch" aria-hidden>{t.swatch.map((c, i) => <span key={i} style={{ background: c }} />)}</span>
              <span className="row" style={{ gap: 6 }}><b>{t.label}</b><span className="faint" style={{ fontSize: 11.5 }}>{t.mode}</span></span>
              <span className="faint" style={{ fontSize: 11.5, lineHeight: 1.35 }}>{t.note}</span>
            </button>
          ))}
        </div>
        <label className="row" style={{ gap: 10 }}>
          <Switch on={a.expressive} onChange={(v) => setA({ ...a, expressive: v })} label="Expressive touches" />
          <span><Sparkles size={13} style={{ verticalAlign: -2 }} /> Expressive touches <span className="faint">— a few emoji where they help (greetings, a run that finished, nothing failed)</span></span>
        </label>
        <label className="row" style={{ gap: 10 }}>
          <Switch on={a.motion} onChange={(v) => setA({ ...a, motion: v })} label="Motion" />
          <span><Wind size={13} style={{ verticalAlign: -2 }} /> Motion <span className="faint">— quiet transitions; your system's reduced-motion setting always wins</span></span>
        </label>
      </div>
    </Card>
  );
}
