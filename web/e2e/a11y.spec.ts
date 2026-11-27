/**
 * Accessibility: every main page passes axe (WCAG 2.1 A / AA rules) with no serious or critical violation, in the
 * dark default and in the light theme. Charts' canvases are left out (uPlot draws; the values are in the tables).
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const enc = encodeURIComponent;
const PAGES = [
  "/", "/runs", `/runs/${enc("local:baseline-cl4f-s0")}`, `/runs/${enc("local:xf-pipe-finetune")}?tab=metrics`,
  `/compare?baseline=${enc("local:baseline-cl4f-s2")}&candidate=${enc("local:query-1800-spiky")}`,
  "/progression", "/studies", "/ledger", "/knowledge", "/settings", "/check", "/local", "/tracks",
];

async function violations(page: Page): Promise<string[]> {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).exclude(".uplot").analyze();
  return r.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.length}× — ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
}

for (const theme of ["midnight", "daylight"]) {
  test(`a11y: main pages, ${theme}`, async ({ page }) => {
    test.setTimeout(180_000);
    await page.addInitScript((t) => {
      localStorage.setItem("mf.tour.done", "1");
      localStorage.setItem("mf.appearance", JSON.stringify({ theme: t, expressive: false, motion: false }));
    }, theme);
    const found: string[] = [];
    for (const url of PAGES) {
      await page.goto(url);
      await page.waitForTimeout(1500);
      found.push(...(await violations(page)).map((v) => `${url}: ${v}`));
    }
    expect(found, found.join("\n")).toEqual([]);
  });
}
