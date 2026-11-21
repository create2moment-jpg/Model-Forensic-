import { expect, test, type Page } from "@playwright/test";

/** The main user journeys against the deterministic demo data. Every page must render without console errors. */

const RUN = "local:xf-pipe-finetune";
const BASE = "local:xf-online-baseline";
const enc = encodeURIComponent;

let errors: string[] = [];

test.beforeEach(async ({ page }) => {
  errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  await page.addInitScript(() => { try { localStorage.setItem("mf.tour.done", "true"); } catch { /* ignore */ } });
});

test.afterEach(() => { expect(errors, errors.join("\n")).toEqual([]); });

async function open(page: Page, path: string) {
  await page.goto(path);
  await expect(page.locator(".page").first()).toBeVisible();
}

test("1 command center shows an insight and recent runs", async ({ page }) => {
  await open(page, "/");
  await expect(page.getByRole("heading", { name: "Command center" })).toBeVisible();
  await expect(page.getByText("Insight on your newest run")).toBeVisible();
  await expect(page.locator(".story").first()).toBeVisible();
  await expect(page.getByText("Recent runs")).toBeVisible();
});

test("2 search runs with a structured query", async ({ page }) => {
  await open(page, `/runs?q=${enc("user:dana")}`);
  await expect(page.getByText(/^\d+ runs · /)).toBeVisible();
  const users = await page.locator("tbody tr td:nth-child(5)").allInnerTexts();
  expect(users.length).toBeGreaterThan(0);
  expect(new Set(users.map((u) => u.trim()))).toEqual(new Set(["dana"]));
});

test("3 run overview tells the story and shows stages", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}`);
  await expect(page.getByText("Pipeline stages")).toBeVisible();
  await expect(page.locator(".story").first()).toBeVisible();
  await expect(page.getByText("train_finetune").first()).toBeVisible();
});

test("4 metrics tab renders charts", async ({ page }) => {
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}?tab=metrics`);
  await expect(page.locator(".uplot").first()).toBeVisible();
});

test("5 compare two runs: deltas and config diff", async ({ page }) => {
  await open(page, `/compare?baseline=${enc(BASE)}&candidate=${enc(RUN)}`);
  await expect(page.getByText(/config/i).first()).toBeVisible();
  await expect(page.locator("table").first()).toBeVisible();
});

test("6 analyze a run produces findings", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=findings&analyze=1`);
  await expect(page.locator(".finding, .finding-card, [data-finding]").first()).toBeVisible({ timeout: 30_000 });
});

test("7 check my run: plain-language cards", async ({ page }) => {
  await open(page, `/check?run=${enc(RUN)}`);
  await expect(page.getByText("Something needs attention before you trust this run.").first()).toBeVisible();
  await expect(page.getByText("Training has no validation metrics").first()).toBeVisible();
});

test("8 guided study: template → runs → pre-register → decide", async ({ page }) => {
  await open(page, "/study-wizard");
  await page.getByText("Is my change better?", { exact: false }).first().click();
  await expect(page.getByText(/candidate/i).first()).toBeVisible();
});

test("9 study book is self-contained HTML with a verdict", async ({ page, request }) => {
  const st = await request.post("/api/v1/studies", { data: { title: "E2E study", goal: "e2e", runs: [RUN], baseline: BASE } });
  expect(st.ok()).toBeTruthy();
  const id = (await st.json()).id as string;
  const job = (await (await request.post(`/api/v1/studies/${id}/book`)).json()).job_id as string;
  await expect.poll(async () => (await (await request.get(`/api/v1/jobs/${job}`)).json()).status, { timeout: 60_000 }).toBe("done");
  await page.goto(`/api/v1/studies/${id}/book`);
  await expect(page.getByText("ruler artifact").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Print / save as PDF" })).toBeVisible();
});

test("10 AI investigation answers offline with grounded claims", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=ai`);
  const box = page.locator("textarea").first();
  await box.fill("Why is the headline score lower than the baseline?");
  await page.getByRole("button", { name: /ask|investigate|run/i }).first().click();
  await expect(page.getByText(/verified|claims|evidence/i).first()).toBeVisible({ timeout: 30_000 });
});

test("11 progression compares many runs", async ({ page }) => {
  await open(page, "/progression");
  await page.getByRole("button", { name: "Add all" }).click();
  await expect(page.locator(".uplot").first()).toBeVisible({ timeout: 20_000 });
});

test("12 settings and diagnostics show workspaces and status", async ({ page }) => {
  await open(page, "/settings");
  await expect(page.getByText(/Azure/).first()).toBeVisible();
  await open(page, "/diagnostics");
  await expect(page.getByText("Status")).toBeVisible();
  await expect(page.locator(".pill", { hasText: /^(ok|degraded)$/ })).toBeVisible();
});

test("13 unknown run shows a readable error, not a crash", async ({ page }) => {
  await open(page, `/runs/${enc("local:does-not-exist")}`);
  await expect(page.locator(".error-box, .empty").first()).toBeVisible();
});
