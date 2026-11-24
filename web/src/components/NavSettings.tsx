import { Compass } from "lucide-react";
import { PLACES, useNavMode } from "../lib/places";
import { Card } from "./ui";

/** Settings → Navigation: five places (simplified) or every page its own sidebar entry (classic). Every page and
 *  link works the same in both; only the sidebar differs. */
export function NavSettings() {
  const [mode, setMode] = useNavMode();
  return (
    <Card title={<span className="row"><Compass size={15} /> Navigation</span>} sub="Only the sidebar changes — every page and link works the same in both">
      <div className="col" style={{ gap: 10 }} data-testid="nav-settings">
        <div className="seg" role="group" aria-label="Navigation">
          <button type="button" className={mode === "simplified" ? "on" : ""} aria-pressed={mode === "simplified"} onClick={() => setMode("simplified")}>Simplified</button>
          <button type="button" className={mode === "classic" ? "on" : ""} aria-pressed={mode === "classic"} onClick={() => setMode("classic")}>Classic</button>
        </div>
        <div className="faint" style={{ fontSize: 12.5 }}>
          {mode === "simplified"
            ? <>Five places; each shows its sections above the page: {PLACES.filter((p) => p.id !== "settings").map((p) => `${p.label} (${p.sections.map((s) => s.label).join(", ")})`).join(" · ")}. Ask and Jobs are in the top bar.</>
            : <>Every page is its own entry in the sidebar, with the Simple / Standard / Expert switch on top.</>}
        </div>
      </div>
    </Card>
  );
}
