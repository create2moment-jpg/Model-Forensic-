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
  const users = await page.locator("tbody tr td:nth-child(6)").allInnerTexts();
  expect(users.length).toBeGreaterThan(0);
  expect(new Set(users.map((u) => u.trim()))).toEqual(new Set(["dana"]));
});

test("3 run overview tells the story and shows stages", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}`);
  await expect(page.locator(".card-head", { hasText: "Pipeline stages" })).toBeVisible();
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

test("21 artifact catalog: families by question, plan a family, prefetch it", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=artifacts`);
  const card = page.locator(".card", { has: page.getByText("What's in this run") }).first();
  await expect(card.getByText("composed configs")).toBeVisible({ timeout: 30_000 });
  await expect(card.getByText("false-positive analysis")).toBeVisible();
  const row = card.locator("tbody tr", { has: page.getByRole("button", { name: "Download" }) }).first();
  await row.getByRole("button", { name: "Download" }).click();
  await expect(page).toHaveURL(/\/prefetch\?task=/, { timeout: 30_000 });
  await expect(page.locator(".pill", { hasText: "planned" }).first()).toBeVisible();
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.locator(".pill", { hasText: "completed" }).first()).toBeVisible({ timeout: 30_000 });
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

test("26 runs: warm up a run (wheel → tick, kept after reload), filter by experiment and my experiments", async ({ page }) => {
  await open(page, "/runs");
  const row = page.locator("tbody tr", { hasText: "completed" }).first();
  const name = (await row.locator("td").nth(1).locator("div").first().innerText()).trim();
  const btn = row.locator(".warm-btn");
  // Earlier journeys may already have opened (and so cached) this run: idle or cached are both valid starts.
  await expect(btn).toHaveAttribute("aria-label", /Warm up|Metadata cached/);
  await btn.click();
  await expect(btn).toHaveAttribute("aria-label", /Metadata cached/, { timeout: 30_000 });
  await page.reload();
  await expect(page.locator("tbody tr", { hasText: name }).first().locator(".warm-btn")).toHaveAttribute("aria-label", /Metadata cached/, { timeout: 30_000 });
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

test("28 prefetch: plan from a run, change the selection, start, see it complete", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}`);
  await page.getByRole("button", { name: "Bundle" }).click();
  await expect(page).toHaveURL(/\/prefetch\?task=/, { timeout: 30_000 });
  await expect(page.getByText("left to fetch").first()).toBeVisible();
  await page.getByRole("button", { name: "all", exact: true }).click();
  await expect(page.locator(".filter-chip.on")).toHaveText("all");
  await expect(page.locator("tbody tr input[type=checkbox]:not(:checked)").first()).toBeVisible();  // the "all" view
  const boxes = page.locator("tbody tr input[type=checkbox]");
  const n = await boxes.count();
  let idx = 0;
  while (idx < n && !(await boxes.nth(idx).isChecked())) idx++;
  await boxes.nth(idx).uncheck();
  await expect(boxes.nth(idx)).not.toBeChecked();
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.locator(".card .pill", { hasText: "completed" }).first()).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: /Runs/ }).first().click();  // leave and come back: the task is kept
  await page.goto("/prefetch");
  await expect(page.locator(".prefetch-row").first()).toBeVisible();
});

test("29 projects: the header selector scopes the run list to one project", async ({ page }) => {
  await open(page, "/runs");
  await expect(page.getByText(/\d+ runs ·/)).toBeVisible({ timeout: 15_000 });
  const total = await page.locator("tbody tr").count();
  expect(total).toBeGreaterThan(10);
  await page.getByLabel("Project").selectOption("detection");
  await expect(async () => {
    const rows = page.locator("tbody tr");
    const n = await rows.count();
    expect(n).toBeGreaterThan(0);
    expect(n).toBeLessThan(total);
    for (const t of await rows.locator("td:nth-child(5)").allInnerTexts()) expect(t).toContain("sparse_detection");
  }).toPass({ timeout: 15_000 });
  await page.getByLabel("Project").selectOption("");
  await expect(page.locator("tbody tr")).toHaveCount(total);
});

test("30 settings: storage paths are validated before saving, workers apply immediately", async ({ page }) => {
  await open(page, "/settings");
  const card = page.locator(".card", { has: page.getByText("Storage & performance") });
  await expect(card.getByText("Downloaded files", { exact: true })).toBeVisible({ timeout: 15_000 });
  await card.getByLabel("meta path").fill("/etc/hostname/meta");  // under a file: can never be created
  await card.getByLabel("meta path").blur();
  await expect(card.getByText(/cannot be created: .* is a file/)).toBeVisible({ timeout: 10_000 });
  await expect(card.getByRole("button", { name: "Save paths" })).toBeDisabled();
  await card.getByRole("button", { name: "Reset" }).click();
  await card.getByRole("button", { name: /Recommended/ }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await card.getByText("Which files are safe to delete").click();
  await expect(card.getByText("forensic.sqlite", { exact: true })).toBeVisible();
});

test("31 run timeline: stages with measured / estimated durations, evidence, code diff", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=timeline`);
  await expect(page.getByText("Current stage")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".tl-row")).toHaveCount(6);
  const train = page.locator(".card", { has: page.locator("text=train_finetune") }).filter({ hasText: "backend timestamps" }).first();
  await expect(train.getByText("training", { exact: true })).toBeVisible();
  await train.getByRole("button", { name: "code diff" }).click();
  await expect(page.locator("pre.diff .add").first()).toBeVisible();
  await expect(page.getByText(/raw evidence/)).toBeVisible();
});

test("32 me: add a run's owner to Me, then Mine finds their runs", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}`);
  await page.getByRole("button", { name: "Add to Me" }).click();
  await expect(page.locator(".page-head .tag", { hasText: /^me$/ })).toBeVisible();
  await open(page, "/settings");
  const card = page.locator(".card", { has: page.getByText("Who is “me”") });
  await expect(card.locator(".tag", { hasText: "dana" })).toBeVisible();
  await open(page, "/runs");
  await page.getByRole("button", { name: "Mine", exact: true }).click();
  await expect(page.locator("tbody tr").first()).toContainText("dana", { timeout: 15_000 });
  await open(page, "/settings");
  await page.locator(".card", { has: page.getByText("Who is “me”") }).getByLabel("remove dana").click();
  await expect(page.locator(".card", { has: page.getByText("Who is “me”") }).locator(".tag", { hasText: "dana" })).toHaveCount(0);
});

test("33 run opens progressively: shell first, every part a visible task, state survives a page change", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}`);
  await expect(page.locator("h1")).toBeVisible();
  const panel = page.getByTestId("run-loading");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText(/Loaded \d+\/\d+ part/, { timeout: 20_000 });
  await panel.getByRole("button", { name: "Full" }).click();
  await expect(panel.getByRole("button", { name: "Full" })).toHaveAttribute("aria-pressed", "true");
  await expect(panel).toContainText(/Loaded \d+\/\d+ part/, { timeout: 20_000 });
  await expect(async () => {  // the panel auto-collapses when loading ends; open it once it has settled
    const show = page.getByRole("button", { name: /Show loading details/ });
    if (await show.isVisible()) await show.click();
    await expect(panel.locator("tr[data-state=done]").first()).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  await expect(panel.getByText("heavy").first()).toBeVisible(); // the cost class is shown, with why on hover
  await page.getByRole("tab", { name: /Metrics/ }).click().catch(() => page.getByText("Metrics", { exact: true }).first().click());
  await expect(page.locator(".uplot").first()).toBeVisible({ timeout: 20_000 });
  // tasks belong to the application: a different page and back keeps them (no reload of loaded parts)
  await open(page, "/runs");
  await open(page, `/runs/${enc(RUN)}`);
  await expect(panel).toContainText(/Loaded \d+\/\d+ part/);
  await expect(page.getByTestId("task-indicator")).toBeVisible();
});

test("34 runs table has a copyable, searchable Run ID column", async ({ page }) => {
  await open(page, "/runs");
  await expect(page.locator("thead th", { hasText: "Run ID" })).toBeVisible();
  const first = (await page.locator("tbody tr td.run-id .mono").first().innerText()).trim();
  expect(first.length).toBeGreaterThan(0);
  await page.locator("input.input.mono").fill(first);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.locator("tbody tr td.run-id .mono").first()).toHaveText(first);
});

test("35 prefetch is observable: phases, live strip, structured events, next step", async ({ page }) => {
  await open(page, `/runs/${enc(BASE)}`);
  await page.getByRole("button", { name: "Bundle" }).click();
  await expect(page).toHaveURL(/\/prefetch\?task=/, { timeout: 30_000 });
  const phases = page.getByTestId("pf-phases");
  await expect(phases).toHaveAttribute("data-phase", "awaiting_selection");
  await expect(page.getByTestId("pf-live")).toContainText(/Next:.*press Start/);
  await page.getByRole("button", { name: "Start" }).click();
  await expect(phases).toHaveAttribute("data-phase", "completed", { timeout: 30_000 });
  await expect(page.getByTestId("pf-live")).toContainText(/Next:.*every selected file is in the cache/);
  const events = page.getByTestId("pf-events");
  await expect(events.locator("[data-kind=phase]").first()).toBeVisible();
  await expect(events).toContainText("validating → completed");
});

test("36 runs: metric columns come from the data, never a hard-coded list", async ({ page }) => {
  await open(page, "/runs");
  await expect(page.locator("tbody tr").first()).toBeVisible();
  const heads = (await page.locator("thead th").allInnerTexts()).map((h) => h.trim());
  const lower = heads.map((h) => h.toLowerCase());
  const metric = heads.slice(lower.indexOf("compute") + 1, lower.indexOf("duration"));
  expect(metric.length).toBeGreaterThan(0);
  await page.getByRole("button", { name: /Metric columns/ }).click();
  const offered = (await page.locator(".palette .metric-row .mono").allInnerTexts()).map((x) => x.trim());
  for (const m of metric) expect(offered).toContain(m); // every shown column is one the runs actually have
});

test("37 test space: production timings, an experiment with a hypothesis, a proposal to review", async ({ page }) => {
  await open(page, "/test-space");
  await expect(page.getByTestId("ts-production")).toContainText("run.metrics");
  await page.getByLabel("Task kind").selectOption("run.metrics");
  await page.getByLabel("Repetitions").fill("5");
  await page.getByLabel("Hypothesis").selectOption("cost_class");
  await page.getByLabel("Sample runs").fill(RUN);
  await page.getByRole("button", { name: "Run experiment" }).click();
  await expect(page.getByTestId("ts-verdict")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("ts-experiment")).toContainText("cold p50");
  await page.getByRole("button", { name: "Propose a change" }).click();
  const props = page.getByTestId("ts-proposals");
  await expect(props.locator("[data-status=proposed]").first()).toBeVisible({ timeout: 15_000 });
  await props.getByRole("button", { name: "Reject" }).first().click(); // reviewed, kept as history
  await expect(props.locator("[data-status=rejected]").first()).toBeVisible();
});

test("38 plots, uncertainty weighting and setup clues", async ({ page }) => {
  await open(page, `/runs/${enc("local:xf-sparse-uw")}?tab=plots`);
  await expect(page.getByTestId("plot-view").first()).toBeVisible({ timeout: 20_000 });
  await page.getByLabel("Plot spec").fill(JSON.stringify({ id: "bad", title: "bad", series: [{ run: "local:xf-sparse-uw", key: "val/invented" }] }));
  await page.getByRole("button", { name: /Validate/ }).click();
  await expect(page.getByTestId("plot-errors")).toContainText("is not a metric of");
  await page.getByRole("button", { name: "Suggest plots" }).click();
  await expect(page.getByText(/no LLM connected/)).toBeVisible(); // offline: rule-based, never invented
  await open(page, `/runs/${enc("local:xf-sparse-uw")}?tab=uncertainty`);
  const uw = page.getByTestId("uncertainty");
  await expect(uw.locator("tr[data-status=switched_off]").first()).toContainText("vel", { timeout: 20_000 });
  await expect(uw.getByTestId("plot-view").first()).toBeVisible();
  await open(page, `/runs/${enc("local:xf-sparse-uw")}?tab=setup`);
  await expect(page.getByTestId("setup-clues")).toContainText("Optimizer");
  await expect(page.getByTestId("setup-clues")).toContainText("effective batch");
});

test("39 diagnostics: configuration checks with fix, prompt and validation", async ({ page }) => {
  await open(page, "/diagnostics");
  const c = page.getByTestId("config-checks");
  await expect(c.locator("[data-check=CFG-WS-1]")).toHaveAttribute("data-status", "pass");
  await expect(c.locator("[data-check=CFG-GIT-1]")).toBeVisible();
  const failing = c.locator("[data-status=fail]").first();
  if (await failing.count()) await expect(failing).toContainText("setup-agent/prompts/");
});

test("40 metrics load as a background task: from the run page and for selected runs", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mf.run.mode", JSON.stringify("light")));
  await open(page, `/runs/${enc("local:lr-sweep-1e3")}`);
  const panel = page.getByTestId("run-loading");
  await expect(panel).toContainText(/Loaded \d+\/\d+ part/, { timeout: 20_000 });
  const btn = panel.getByRole("button", { name: "Load metrics in background" });
  if (await btn.isVisible()) {
    await btn.click();
    await expect(panel).toContainText(/Metric histories|Loaded/);
  }
  await open(page, "/runs");
  await page.locator("tbody tr input[type=checkbox]").first().check();
  await page.getByRole("button", { name: "Load metrics" }).click();
  await expect(page.getByText(/Loading metrics of 1 run/)).toBeVisible();
});

test("41 light mode opens a run without heavy work: check and comparison run when asked", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mf.run.mode", JSON.stringify("light")));
  await open(page, `/runs/${enc(RUN)}`);
  await expect(page.getByTestId("story-idle")).toBeVisible();
  await page.getByRole("button", { name: "Check this run" }).click();
  await expect(page.locator(".story .story-verdict")).toBeVisible({ timeout: 20_000 });
  const idle = page.getByTestId("comparison-idle");
  if (await idle.isVisible()) {
    await idle.getByRole("button", { name: /Compare with the baseline/ }).click();
    await expect(idle).toBeHidden({ timeout: 30_000 });
  }
});

test("42 a run opens light: a short headline, Full only for this run view", async ({ page }) => {
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}`);
  const cards = page.getByTestId("headline").locator(".card.stat");
  await expect(cards.first()).toBeVisible();
  expect(await cards.count()).toBeLessThanOrEqual(6);
  const panel = page.getByTestId("run-loading");
  await panel.getByRole("button", { name: "Full" }).click();
  await expect(panel.getByRole("button", { name: "Full" })).toHaveAttribute("aria-pressed", "true");
  await open(page, `/runs/${enc("local:lr-sweep-1e3")}`);  // the next run opens light again
  await expect(page.getByTestId("run-loading").getByRole("button", { name: "Light" })).toHaveAttribute("aria-pressed", "true");
});

test("43 pipeline metrics: stage / metric, stages can be switched off", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=metrics`);
  const toggles = page.getByTestId("stage-toggles");
  await expect(toggles).toBeVisible({ timeout: 20_000 });
  const first = toggles.locator(".filter-chip").first();
  const stage = (await first.innerText()).trim();
  await expect(page.locator(".palette-group", { hasText: `${stage} /` }).first()).toBeVisible();
  await first.click();
  await expect(first).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".palette-group", { hasText: `${stage} /` })).toHaveCount(0);
  await first.click();  // back on (the choice is remembered by stage name)
  await expect(page.locator(".palette-group", { hasText: `${stage} /` }).first()).toBeVisible();
});

test("44 tables sort by clicking a header: ascending, descending, original order", async ({ page }) => {
  await open(page, `/runs/${enc("local:xf-sparse-uw")}?tab=uncertainty`);
  const table = page.getByTestId("uncertainty").locator("table.t").first();
  await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
  const shareCol = async (): Promise<number[]> => (await table.locator("tbody tr td:nth-child(5)").allInnerTexts()).map((t) => parseFloat(t));
  const before = await shareCol();
  const head = table.locator("thead th", { hasText: "share" });
  await head.click();
  await expect(head).toHaveAttribute("aria-sort", "ascending");
  const asc = await shareCol();
  expect(asc).toEqual([...asc].sort((a, b) => a - b));
  await head.click();
  await expect(head).toHaveAttribute("aria-sort", "descending");
  const desc = await shareCol();
  expect(desc).toEqual([...desc].sort((a, b) => b - a));
  await head.click();
  await expect(head).not.toHaveAttribute("aria-sort", /.+/);
  expect(await shareCol()).toEqual(before);
  // the same works on other tables, e.g. the Test Space production timings (text column)
  await open(page, "/test-space");
  const prod = page.getByTestId("ts-production");
  await prod.locator("thead th", { hasText: "Task" }).click();
  const names = await prod.locator("tbody tr td:first-child").allInnerTexts();
  expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })));
});

test("45 charts: zoom in / out / reset and full screen", async ({ page }) => {
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}?tab=metrics`);
  const chart = page.locator(".chart-card").first();
  await expect(chart.locator(".uplot")).toBeVisible({ timeout: 20_000 });
  const ticks = async (c = chart): Promise<string> => (await c.locator(".chart").getAttribute("data-xrange")) ?? "";
  const before = await ticks();
  await chart.hover();
  await chart.getByRole("button", { name: "Zoom in" }).click();
  await expect.poll(() => ticks()).not.toBe(before); // the x axis shows a narrower range
  await chart.getByRole("button", { name: "Reset zoom" }).click();
  await expect.poll(() => ticks()).toBe(before);
  await chart.getByRole("button", { name: "Zoom in" }).click();
  const zoomed = await ticks();
  await chart.getByRole("button", { name: "Full screen" }).click();
  const full = page.getByTestId("chart-full");
  await expect(full).toBeVisible();
  expect((await full.boundingBox())!.height).toBeGreaterThan(500);
  await expect.poll(() => ticks(full)).not.toBe(before); // the zoom is kept in full screen
  void zoomed;
  await page.keyboard.press("Escape");
  await expect(full).toBeHidden();
});
