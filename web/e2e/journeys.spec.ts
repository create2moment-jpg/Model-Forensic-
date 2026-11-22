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

test("14 explain this chart: markers, reading and what to check", async ({ page }) => {
  await open(page, `/runs/${enc("local:lr-sweep-1e3")}?tab=metrics`);
  const card = page.locator(".card", { has: page.locator(".chart-title", { hasText: /^train\/loss/ }) }).first();
  await expect(card.getByText(/NaN\/Inf/).first()).toBeVisible();
  await card.getByRole("button", { name: "Explain" }).click();
  await expect(card.getByText("What this chart shows")).toBeVisible();
  await expect(card.getByText(/non-finite value/).first()).toBeVisible();
  await expect(card.getByText("What to check")).toBeVisible();
  await card.getByRole("button", { name: "Ask" }).click();
  await expect(card.locator(".ci-answer")).toBeVisible();
});

test("15 progression: explain a multi-run chart", async ({ page }) => {
  const runs = ["local:baseline-cl4f-s0", "local:baseline-cl4f-s1", "local:lr-sweep-1e3"].join(",");
  await open(page, `/progression?runs=${enc(runs)}&metrics=${enc("train/loss")}`);
  const card = page.locator(".card", { has: page.locator(".chart-title", { hasText: /^train\/loss/ }) }).first();
  await card.getByRole("button", { name: "Explain" }).click();
  await expect(card.getByText(/Across 3 runs/)).toBeVisible();
  await expect(card.getByText(/hit NaN\/Inf: lr-sweep-1e3/)).toBeVisible();
});

test("16 compare 4 runs: where and why they diverge", async ({ page }) => {
  const more = ["local:lr-sweep-1e3", "local:baseline-cl4f-s1"].join(",");
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&more=${enc(more)}`);
  const card = page.locator(".card", { has: page.getByText("Curves — where and why the runs diverge") }).first();
  await expect(card.getByText(/run\(s\) diverge from the reference on train\/loss/)).toBeVisible({ timeout: 30_000 });
  await expect(card.locator(".pill", { hasText: "behind" }).first()).toBeVisible();
  await expect(card.locator(".pill", { hasText: "same (within noise)" }).first()).toBeVisible();
  await card.getByRole("button", { name: "Ask" }).click();
  await expect(card.locator(".ci-answer")).toBeVisible();
});

test("17 a study draft is kept on the server: leave, reload, continue, discard", async ({ page }) => {
  await open(page, `/studies?runs=${enc("local:baseline-cl4f-s0,local:cand-radar-8f")}`);
  await expect(page).toHaveURL(/draft=new-study/);
  await page.getByPlaceholder("Why does the radar branch").fill("Does radar help far away?");
  await expect(page.getByText(/draft saved/)).toBeVisible({ timeout: 10_000 });
  await page.getByRole("link", { name: /Settings/ }).first().click();
  await expect(page.locator(".page").first()).toBeVisible();
  await page.goto("/");  // a full reload: nothing left in the page's memory
  const cont = page.locator(".card", { has: page.getByText("Continue where you left off") });
  await expect(cont.getByText("Does radar help far away?")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: /^Studies/ }).first().click();
  const row = page.locator(".draft-row", { hasText: "Does radar help far away?" });
  await row.getByRole("link", { name: "Continue" }).click();
  await expect(page.getByPlaceholder("Why does the radar branch")).toHaveValue("Does radar help far away?");
  await expect(page.getByText("local:cand-radar-8f")).toBeVisible();
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: /Discard draft/ }).click();
  await expect(page.getByPlaceholder("Why does the radar branch")).toHaveCount(0);
  await expect(page.locator(".draft-row", { hasText: "Does radar help far away?" })).toHaveCount(0);
  await page.getByRole("button", { name: "New study" }).click();
  await expect(page.getByPlaceholder("Why does the radar branch")).toHaveValue("");
});

test("18 dataset and compute per pipeline stage", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=dataset`);
  await expect(page.getByText("evaluate_finetune").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/config requests version 14, the log shows .*:15 loaded/)).toBeVisible();
  await expect(page.getByText("SECRET123")).toHaveCount(0);
  await open(page, `/runs/${enc(RUN)}?tab=compute`);
  await expect(page.getByText(/8× NVIDIA A100-SXM4-80GB/).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("bf16-mixed").first()).toBeVisible();
});

test("19 precision & recall: working points, AUC by distance, log lines, file preview", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=precision`);
  await expect(page.getByText("Working points", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("PR-curve AUC by distance")).toBeVisible();
  await page.getByRole("button", { name: "Search all logs" }).click();
  await expect(page.getByText(/expected calibration error/).first()).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Preview" }).first().click();
  await expect(page.locator(".preview-text")).toBeVisible({ timeout: 30_000 });
});

test("20 compare: seed noise verdicts and save to a study", async ({ page, request }) => {
  const st = await (await request.post("/api/v1/studies", { data: { title: "Seed check", runs: [] } })).json();
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&more=${enc("local:priya-lr3e4-wd05")}`);
  const card = page.locator(".card", { has: page.getByText("Curves — where and why the runs diverge") }).first();
  await expect(card.getByText(/noise floor from 3 reference seeds/)).toBeVisible({ timeout: 30_000 });
  await expect(card.locator(".pill", { hasText: /^beyond/ }).first()).toBeVisible();
  await expect(card.locator(".pill", { hasText: /^within/ }).first()).toBeVisible();
  await card.locator("select").last().selectOption(st.id);
  await card.getByRole("button", { name: "Save to study" }).click();
  await card.getByText(/Saved — open the study/).click();
  await expect(page.getByText("baseline-cl4f-s0", { exact: true }).first()).toBeVisible();
});

test("21 artifact catalog: families by question, download one family", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=artifacts`);
  const card = page.locator(".card", { has: page.getByText("What's in this run") }).first();
  await expect(card.getByText("composed configs")).toBeVisible({ timeout: 30_000 });
  await expect(card.getByText("false-positive analysis")).toBeVisible();
  const row = card.locator("tbody tr", { has: page.getByRole("button", { name: "Download" }) }).first();
  const family = (await row.locator("td b").innerText()).trim();
  await row.getByRole("button", { name: "Download" }).click();
  const same = card.locator("tbody tr", { has: page.locator("b", { hasText: family }) });
  await expect(same.locator(".pill", { hasText: "all" })).toBeVisible({ timeout: 30_000 });
});

test("22 compare a pipeline stage against a baseline (explicit stage mapping)", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:xf-online-baseline")}&candidate=${enc(RUN)}`);
  const card = page.locator(".card", { has: page.getByText("Curves — where and why the runs diverge") }).first();
  await expect(card.getByText("R2")).toBeVisible({ timeout: 30_000 });
  await card.locator("select.stage-pick").selectOption("evaluate_pretrain");
  await expect(page).toHaveURL(/stages=/);
  await expect(card.getByText(/· evaluate_pretrain/).first()).toBeVisible({ timeout: 30_000 });
});

test("23 knowledge: search the documentation, read the section, see the sources", async ({ page }) => {
  await open(page, "/knowledge?tab=docs");
  await page.getByLabel("Search documentation").fill("negative sigma");
  const hit = page.locator(".doc-hit", { hasText: "Why the weighted loss can be negative" });
  await expect(hit).toBeVisible({ timeout: 30_000 });
  await hit.click();
  await expect(page.locator(".doc-view")).toContainText("a negative total is not divergence");
  await expect(page).toHaveURL(/doc=inbox/);
  await page.getByRole("tab", { name: /Sources/ }).click();
  await expect(page.getByText("Tell your coding agents where to write")).toBeVisible();
  await expect(page.locator("tbody tr", { hasText: "inbox" }).first()).toContainText("entries + docs");
});

test("24 studies: filter open / closed, search on the server", async ({ page }) => {
  await open(page, "/studies");
  await page.getByRole("button", { name: "New study" }).click();
  await page.getByPlaceholder("Why does the radar branch").fill("Searchable study about lidar");
  await page.getByRole("button", { name: /Create study/ }).click();
  await expect(page).toHaveURL(/\/studies\/st-|\/studies\/[a-z0-9]/);
  await open(page, "/studies");
  await page.getByLabel("Search studies").fill("lidar");
  await expect(page.getByText("Searchable study about lidar")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("tab", { name: "Closed" }).click();
  await expect(page.getByText("Searchable study about lidar")).toHaveCount(0);
});

test("25 experience: write a belief, check it against the runs, read what the tool thinks", async ({ page }) => {
  await open(page, "/knowledge?tab=experience");
  await page.getByRole("button", { name: "Add experience" }).click();
  await page.getByPlaceholder("A higher learning rate hurts the GT finetune").fill("Sigma mutes the radar head (e2e)");
  await page.getByLabel("Check it against the runs").check();
  await page.getByLabel("Kind of claim").selectOption("loss_behaviour");
  await page.getByPlaceholder("radar").fill("radar");
  await page.getByRole("button", { name: "Save and check" }).click();
  const card = page.locator(".belief-card", { hasText: "Sigma mutes the radar head (e2e)" });
  await expect(card).toBeVisible();
  await expect(card.getByText(/checked/)).toBeVisible({ timeout: 60_000 });
  await expect(card).not.toHaveAttribute("data-colour", "grey");
  await card.getByText("What Model Forensic thinks").click();
  await expect(card.locator(".belief-detail")).toContainText("radar is muted");
});

test("26 runs: prefetch a run (wheel → tick, kept after reload), filter by experiment and my experiments", async ({ page }) => {
  await open(page, "/runs");
  const row = page.locator("tbody tr", { hasText: "completed" }).first();
  const name = (await row.locator("td").nth(1).locator("div").first().innerText()).trim();
  const btn = row.locator(".prefetch-btn");
  // Earlier journeys may already have opened (and so cached) this run: idle or cached are both valid starts.
  await expect(btn).toHaveAttribute("aria-label", /Prefetch metadata|Metadata cached/);
  await btn.click();
  await expect(btn).toHaveAttribute("aria-label", /Metadata cached/, { timeout: 30_000 });
  await page.reload();
  await expect(page.locator("tbody tr", { hasText: name }).first().locator(".prefetch-btn")).toHaveAttribute("aria-label", /Metadata cached/, { timeout: 30_000 });
  await page.getByLabel("Experiment").selectOption("sparse_detection");
  await expect(page).toHaveURL(/experiment/);
  await expect(page.locator("tbody tr").first()).toContainText("sparse_detection");
  await page.getByRole("button", { name: "My experiments" }).click();
  await expect(page).toHaveURL(/experiment%3Amine|experiment:mine/);
});

test("27 header: the plug tries the local LLM and says why it is not connected", async ({ page }) => {
  await open(page, "/runs");
  await expect(page.locator(".llm-pill")).toContainText("offline");
  await page.getByRole("button", { name: "Connect the local LLM" }).click();
  await expect(page.getByText(/No server answers at http:\/\/127\.0\.0\.1:9\/v1/)).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".llm-pill")).toContainText("offline");  // nothing switched
});
