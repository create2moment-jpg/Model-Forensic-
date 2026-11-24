/**
 * The documentation's screenshots, regenerated from the demo data — so they always show the current UI.
 *
 *   cd web && npx vite build && MF_SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts --trace off
 *
 * Skipped in the normal test run. Writes into docs/screenshots/ (1440×900, the README's size). Screenshots that need a
 * multi-step setup (study book pages, an AI answer, the connection test, prompt templates) are not regenerated here.
 */
import { expect, test, type Page } from "@playwright/test";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../docs/screenshots");
const enc = encodeURIComponent;
const BASE = "local:baseline-cl4f-s0";
const PIPE = "local:xf-pipe-finetune";

test.skip(!process.env.MF_SCREENSHOTS, "set MF_SCREENSHOTS=1 to regenerate the documentation's screenshots");
test.describe.configure({ mode: "serial" });

async function shot(page: Page, name: string, url: string, ready?: (p: Page) => Promise<void>) {
  await page.goto(url);
  await page.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; } .stale-banner { display: none !important; }" });
  if (ready) await ready(page);
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("mf.tour.done", "1");
    localStorage.setItem("mf.appearance", JSON.stringify({ theme: "midnight", expressive: true, motion: false }));
  });
});

test("screenshots", async ({ page }) => {
  test.setTimeout(240_000);
  const verdictReady = async (p: Page) => { await expect(p.getByTestId("verdict").first()).not.toHaveAttribute("data-state", /loading|not_loaded/, { timeout: 30_000 }); };
  await shot(page, "command-center", "/", verdictReady);
  await shot(page, "home", "/", verdictReady);
  await shot(page, "run-story", `/runs/${enc(PIPE)}`, verdictReady);
  await shot(page, "run-overview", `/runs/${enc(BASE)}`, verdictReady);
  await shot(page, "run-review", `/runs/${enc("local:cand-radar-8f")}?tab=review&baseline=${enc(BASE)}`, async (p) => { await expect(p.getByTestId("expert-review")).toBeVisible({ timeout: 30_000 }); });
  await shot(page, "run-metrics", `/runs/${enc(BASE)}?tab=metrics`, async (p) => { await expect(p.locator(".uplot").first()).toBeVisible({ timeout: 30_000 }); });
  await shot(page, "run-findings", `/runs/${enc("local:cand-radar-8f")}?tab=findings&analyze=1&baseline=${enc(BASE)}`, async (p) => { await expect(p.locator(".finding, .finding-card, [data-finding]").first()).toBeVisible({ timeout: 30_000 }); });
  await shot(page, "run-logs", `/runs/${enc("local:lr-sweep-1e3")}?tab=logs`, async (p) => { await expect(p.getByTestId("log-view")).toBeVisible({ timeout: 30_000 }); });
  await shot(page, "run-artifacts", `/runs/${enc(BASE)}?tab=artifacts`, async (p) => { await p.waitForTimeout(1500); });
  await shot(page, "pipeline-sequences", `/runs/${enc(PIPE)}?tab=sequences`, async (p) => { await p.waitForTimeout(2500); });
  await shot(page, "runs-search", `/runs?q=${enc("failed training jobs from last week")}`, async (p) => { await expect(p.locator("tbody tr").first()).toBeVisible(); });
  await shot(page, "workspace-selector", "/runs", async (p) => { await p.getByTitle("Workspace — scopes search and lists").click(); });
  await shot(page, "compare", `/compare?baseline=${enc("local:baseline-cl4f-s2")}&candidate=${enc("local:query-1800-spiky")}`, async (p) => {
    await expect(p.getByTestId("compare-verdict")).not.toHaveAttribute("data-state", "loading", { timeout: 30_000 });
  });
  await shot(page, "progression", `/progression?runs=${enc([BASE, "local:baseline-cl4f-s1", "local:baseline-cl4f-s2", "local:lr-sweep-1e3", "local:query-1800-spiky"].join(","))}&metrics=${enc("train/loss")}`, async (p) => {
    await expect(p.locator(".uplot").first()).toBeVisible({ timeout: 30_000 });
  });
  await shot(page, "check-my-run", `/check?run=${enc(PIPE)}`, async (p) => { await p.waitForTimeout(2500); });
  await shot(page, "study-wizard", "/study-wizard");
  await shot(page, "settings-azure", "/settings");
  await shot(page, "appearance", "/settings", async (p) => { await p.getByTestId("appearance-settings").scrollIntoViewIfNeeded(); });
  await shot(page, "command-palette", "/", async (p) => {
    await p.keyboard.press("Control+k");
    await p.locator(".palette-input input").fill("baseline");
    await p.waitForTimeout(500);
  });
  await shot(page, "shortcuts", "/runs", async (p) => { await p.locator("h1").click(); await p.keyboard.press("?"); });
  await shot(page, "places", "/ledger");
  // the light theme
  await page.evaluate(() => localStorage.setItem("mf.appearance", JSON.stringify({ theme: "daylight", expressive: true, motion: false })));
  await shot(page, "run-metrics-light", `/runs/${enc(BASE)}?tab=metrics`, async (p) => { await expect(p.locator(".uplot").first()).toBeVisible({ timeout: 30_000 }); });
});
