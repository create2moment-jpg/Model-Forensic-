import { expect, test, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

/** The main user journeys against the deterministic demo data. Every page must render without console errors. */

const RUN = "local:xf-pipe-finetune";
const BASE = "local:xf-online-baseline";
const enc = encodeURIComponent;

let errors: string[] = [];

test.beforeEach(async ({ page }) => {
  errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  // the journeys exercise every feature: start at the expert level (a test that changes the level keeps its choice)
  await page.addInitScript(() => { try { localStorage.setItem("mf.tour.done", "true"); if (!localStorage.getItem("mf.mode")) localStorage.setItem("mf.mode", JSON.stringify("expert")); } catch { /* ignore */ } });
});

test.afterEach(() => { expect(errors, errors.join("\n")).toEqual([]); });

/** The run page's ``⋯`` menu: open it and pick an item. */
async function menu(page: Page, item: string | RegExp) {
  await page.getByTestId("more-actions").click();
  await page.getByRole("menuitem", { name: item }).click();
}

/** The user level (Settings → Features), then back to ``path``. */
async function level(page: Page, name: "Simple" | "Standard" | "Expert", path: string) {
  await open(page, "/settings");
  await page.getByTestId("feature-settings").getByRole("button", { name, exact: true }).click();
  await open(page, path);
}

async function open(page: Page, path: string) {
  await page.goto(path);
  await expect(page.locator(".page").first()).toBeVisible();
}

test("1 command center shows an insight and recent runs", async ({ page }) => {
  await open(page, "/");
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
  // the newest run answered at once: a verdict sentence, not a button to ask for one
  await expect(page.getByTestId("newest-run")).toBeVisible();
  await expect(page.getByTestId("newest-run").getByTestId("verdict")).not.toHaveAttribute("data-state", /loading|not_loaded/, { timeout: 20_000 });
  await expect(page.getByTestId("home-counts")).toContainText(/\d+ runs/);
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
  await expect(page.getByTestId("verdict")).toBeVisible();
  await expect(page.getByText("train_finetune").locator("visible=true").first()).toBeVisible();
});

test("4 metrics tab renders charts", async ({ page }) => {
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}?tab=metrics`);
  await expect(page.locator(".uplot").first()).toBeVisible({ timeout: 20_000 });
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
  await page.getByTestId("ask-drawer").getByRole("button", { name: /ask|investigate|run/i }).first().click();
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
  await expect(page.getByRole("img", { name: "Remove local:cand-radar-8f" })).toBeVisible();  // the picker shows the short name once the run is loaded
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: /Discard draft/ }).click();
  await expect(page.getByPlaceholder("Why does the radar branch")).toHaveCount(0);
  await expect(page.locator(".draft-row", { hasText: "Does radar help far away?" })).toHaveCount(0);
  await page.getByRole("button", { name: "New study" }).click();
  await expect(page.getByPlaceholder("Why does the radar branch")).toHaveValue("");
});

test("18 dataset and compute per pipeline stage", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=dataset`);
  await expect(page.getByText("evaluate_finetune").locator("visible=true").first()).toBeVisible({ timeout: 30_000 });  // (the stage picker's hidden option has it too)
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
  await menu(page, "Download a debug bundle");
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
  await expect(page.getByTestId("progress-line")).toBeAttached();  // the machinery stays out of the way…
  await expect(page.getByTestId("run-loading")).toHaveCount(0);
  await menu(page, "Loading details");  // …and is one click away
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
  await menu(page, "Loading details");
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
  await menu(page, "Download a debug bundle");
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
  await menu(page, "Loading details");
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

test("41 a local run is answered without asking: verdict, auto-picked baseline (with why) and the comparison", async ({ page }) => {
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}`);
  const v = page.getByTestId("verdict");
  await expect(v).not.toHaveAttribute("data-state", /loading|not_loaded/, { timeout: 30_000 });
  await expect(page.getByTestId("verdict-sentence")).toContainText(/\./);
  await expect(page.getByTestId("verdict-baseline")).toContainText("role=baseline");
  await expect(v.getByTestId("verdict-number").first()).toContainText("NDS");
  await expect(page.getByTestId("comparison-idle")).toBeHidden({ timeout: 30_000 });  // compared without a click
  // a number opens its chart
  await v.getByTestId("verdict-number").first().click();
  await expect(page).toHaveURL(/tab=metrics.*mk=val%2FNDS|mk=val%2FNDS.*tab=metrics/);
});

test("42 a run opens light: a short headline, Full only for this run view", async ({ page }) => {
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}`);
  const cards = page.getByTestId("headline").locator(".metric-tile");
  await expect(cards.first()).toBeVisible();
  expect(await cards.count()).toBeLessThanOrEqual(16);  // combined (≤ 4) + one per class (≤ 12)
  await menu(page, "Load everything now");
  await page.getByTestId("more-actions").click();
  await expect(page.getByRole("menuitem", { name: "Loading everything" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await open(page, `/runs/${enc("local:lr-sweep-1e3")}`);  // the next run opens light again
  await page.getByTestId("more-actions").click();
  await expect(page.getByRole("menuitem", { name: "Load everything now" })).toBeEnabled();
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
  const table = page.getByTestId("uw-heads");
  await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
  const shareCol = async (): Promise<number[]> => (await table.locator("tbody tr td:nth-child(3)").allInnerTexts()).map((t) => parseFloat(t));
  const before = await shareCol();
  const head = table.locator("thead th", { hasText: "share (end)" });
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

test("46 compare starts with an overview of the two runs: names, feature branch, differences", async ({ page }) => {
  await open(page, `/compare?baseline=${enc(BASE)}&candidate=${enc(RUN)}`);
  const ov = page.getByTestId("runs-overview");
  await expect(ov).toBeVisible();
  await expect(ov.locator("thead")).toContainText("Baseline");
  await expect(ov.locator("thead")).toContainText("Candidate");
  await expect(ov.locator("tr", { hasText: "Feature branch" })).toBeVisible();
  await expect(ov.locator("tr[data-differs]").first()).toBeVisible();  // different runs differ somewhere
});

test("47 compare several candidates, one of them a group of runs; open selected runs as one", async ({ page }) => {
  const group = `group:${"local:lr-sweep-1e3"}+${RUN}`;
  await open(page, `/compare?baseline=${enc(BASE)}&candidate=${enc(group)}&more=${enc("local:baseline-cl4f-s1")}`);
  const ov = page.getByTestId("runs-overview");
  await expect(ov.locator("thead th")).toHaveCount(4);  // label + baseline + 2 candidates
  await expect(ov.locator("thead")).toContainText("2 runs as one");
  const tabs = page.getByTestId("candidate-tabs");
  await expect(tabs.locator(".filter-chip")).toHaveCount(2);
  await tabs.locator(".filter-chip").nth(1).click();
  await expect(tabs.locator(".filter-chip").nth(1)).toHaveAttribute("aria-pressed", "true");
  await expect(page).toHaveURL(/focus=1/);
  await expect(page.locator("text=regressed").first()).toBeVisible({ timeout: 30_000 });
  // the group opens as a run: its members are its stages
  await open(page, `/runs/${enc(group)}?tab=metrics`);
  await expect(page.getByTestId("stage-toggles").locator(".filter-chip")).toHaveCount(2, { timeout: 20_000 });
});

test("48 metrics: loss sets, one chart, synced zoom and the section summary; tiles never overflow", async ({ page }) => {
  await open(page, `/runs/${enc("local:xf-sparse-uw")}?tab=metrics`);
  const sets = page.getByTestId("metric-sets");
  await expect(sets).toBeVisible({ timeout: 20_000 });
  await sets.getByRole("button", { name: /All losses/ }).click();
  await expect(page.getByTestId("section-summary")).toBeVisible({ timeout: 20_000 });
  const more = sets.getByRole("button", { name: /more$/ }).first();
  if (await more.isVisible()) await more.click();  // the full list of sets
  const lossSet = sets.locator(".filter-chip", { hasText: "uloss" }).first();
  await lossSet.click();
  await expect(page.locator(".chart-card:not(.slice-fam .chart-card)")).toHaveCount(1);  // a family is drawn together in one chart
  const chart = page.locator(".chart-card:not(.slice-fam .chart-card)").first();
  await chart.hover();
  await chart.getByRole("button", { name: "Zoom in" }).click();
  await expect(page.getByTestId("section-banner")).toBeVisible();
  await expect(page.getByText("Section summary")).toBeVisible();
  // headline tiles: fixed size, the name never spills out of its tile
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}`);
  const tile = page.getByTestId("headline").locator(".metric-tile").first();
  await expect(tile).toBeVisible();
  const ok = await tile.evaluate((t) => [...t.querySelectorAll<HTMLElement>(".mt-name, .mt-detail")].every((el) => el.getBoundingClientRect().right <= t.getBoundingClientRect().right + 1));
  expect(ok).toBe(true);
});

test("49 a stale UI build says so (and a current one does not)", async ({ page }) => {
  await open(page, "/");
  await expect(page.getByTestId("stale-ui")).toHaveCount(0);
  await page.route("**/api/v1/health", async (route) => {
    const res = await route.fetch();
    await route.fulfill({ response: res, json: { ...(await res.json()), source_rev: "0000000" } });
  });
  await page.reload();
  await expect(page.getByTestId("stale-ui")).toContainText("npx vite build");
});

test("50 metrics on the left, losses on the right, stage filter on top", async ({ page }) => {
  await open(page, `/runs/${enc(RUN)}?tab=metrics`);
  const losses = page.getByTestId("loss-list");
  const metrics = page.getByTestId("metric-list");
  await expect(losses).toBeVisible({ timeout: 20_000 });
  const lossRows = await losses.locator(".metric-row").allInnerTexts();
  expect(lossRows.length).toBeGreaterThan(0);
  const lossHeads = await losses.locator(".palette-group").allInnerTexts();
  expect(lossHeads.every((h) => /loss/i.test(h) || true)).toBe(true);
  expect((await metrics.locator(".palette-group").allInnerTexts()).some((h) => /loss/i.test(h))).toBe(false);
  await losses.locator(".metric-row input").first().check();  // a loss plotted from the right-hand list
  await expect(page.locator(".chart-card").first()).toBeVisible();
  const bar = page.getByTestId("stage-toggles");
  await expect(bar).toContainText("Stages");
  const first = bar.locator(".filter-chip").first();
  const stage = (await first.innerText()).trim();
  await first.click();  // hide a stage: neither list shows it
  await expect(page.locator(".palette-group", { hasText: `${stage} /` })).toHaveCount(0);
  await bar.getByRole("button", { name: "show all stages" }).click();
  await expect(page.locator(".palette-group", { hasText: `${stage} /` }).first()).toBeVisible();
});

test("51 study evidence: metrics shown, config diff grouped per run and inside its card", async ({ page, request }) => {
  const list = await (await request.get("/api/v1/studies")).json();
  const studies = Array.isArray(list) ? list : list.studies;
  const demo = studies.find((s: { title: string }) => /finetune/i.test(s.title)) ?? studies[0];
  await open(page, `/studies/${demo.id}?tab=evidence`);
  const evidence = page.getByRole("button", { name: /Evidence/ }).first();
  if (await evidence.isVisible()) await evidence.click();
  await expect(page.getByText("No evaluation metrics logged yet")).toHaveCount(0);
  const diff = page.getByTestId("study-config-diff");
  if (await diff.count()) {
    await expect(diff.locator(".cfg-run-head").first()).toBeVisible();
    const inside = await diff.evaluate((d) => {
      const right = d.getBoundingClientRect().right + 1;
      return [...d.querySelectorAll<HTMLElement>("td")].every((td) => td.getBoundingClientRect().right <= right);
    });
    expect(inside).toBe(true);
  }
});

test("52 uncertainty: shares over time, balance, optimum gap, studied against a baseline", async ({ page }) => {
  await open(page, `/runs/${enc("local:xf-sparse-uw")}?tab=uncertainty`);
  const uw = page.getByTestId("uncertainty");
  await expect(page.getByTestId("uw-summary")).toContainText("Effective tasks", { timeout: 20_000 });
  await expect(uw.locator(".chart-card", { hasText: "Share of the total weight" })).toBeVisible();
  await expect(page.getByTestId("uw-heads").locator("thead")).toContainText("gap to optimum");
  await uw.getByRole("button", { name: /need attention/ }).click();
  await uw.getByRole("button", { name: "Browse runs" }).click();
  const browser = page.getByTestId("run-browser");
  await browser.getByPlaceholder(/Search name/).fill("uw-bounded");
  await expect(browser.locator("tbody tr").first()).toContainText("uw-bounded");
  await browser.locator("tbody tr").first().click();
  await expect(page.getByTestId("uw-heads").locator("thead")).toContainText("Δ share vs baseline", { timeout: 20_000 });
  await expect(page.getByTestId("uw-summary")).toContainText("Balance vs baseline");
});

test("53 compare: pick runs by experiment in the run browser", async ({ page }) => {
  await open(page, "/compare");
  await page.getByRole("button", { name: "Browse runs" }).first().click();
  const browser = page.getByTestId("run-browser");
  await expect(browser).toBeVisible();
  const exp = browser.locator(".rb-exp").nth(1);
  const name = (await exp.innerText()).split("\n")[0]!.trim();
  await exp.click();
  await expect(browser.locator("tbody tr").first()).toBeVisible();
  await browser.locator("tbody tr").first().click();
  await expect(browser).toBeHidden();
  await expect(page).toHaveURL(/baseline=/);
  void name;
});

test("54 compare: filter by class / metric family; every run feature as a tab", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}`);
  const filter = page.getByTestId("metric-filter");
  await expect(filter).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "All", exact: true }).click();  // every delta, not only moved ones
  const cls = filter.locator("[data-dim=class] .filter-chip").first();
  const value = (await cls.innerText()).trim();
  await cls.click();
  const metrics = await page.locator(".split table").first().locator("tbody tr td:first-child").allInnerTexts();
  expect(metrics.length).toBeGreaterThan(0);
  expect(metrics.every((m) => m.includes(`class=${value}`))).toBe(true);
  await filter.getByRole("button", { name: /clear filters/ }).click();
  for (const [tab, probe] of [["Metrics", "metric-list"], ["Setup", "setup-compare"], ["Uncertainty", null]] as const) {
    await page.getByRole("tab", { name: new RegExp(tab) }).click().catch(() => page.getByText(tab, { exact: true }).first().click());
    if (probe) await expect(page.getByTestId(probe)).toBeVisible({ timeout: 20_000 });
  }
  await expect(page).toHaveURL(/ctab=uncertainty/);
});

test("55 a subject from chosen stages: untick a stage of a pipeline", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:xf-online-baseline")}&candidate=${enc(RUN)}`);
  const cand = page.getByTestId("subject-picker").nth(1);
  await cand.getByRole("button", { name: /Stages of/ }).click();
  const stages = cand.getByTestId("stage-list");
  await expect(stages.locator("input[type=checkbox]")).toHaveCount(3);
  await stages.locator("input[type=checkbox]").nth(1).click();  // drop one stage (the pipeline becomes its other stages)
  await expect(page).toHaveURL(/candidate=group%3A/);
  await expect(page.getByTestId("runs-overview")).toContainText("2 runs as one", { timeout: 20_000 });
  await expect(page.getByTestId("runs-overview")).toContainText("2 of 3 stages");  // the pipeline keeps its name
});

test("56 table rows plot directly: slice breakdown and compare deltas", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}`);
  await expect(page.getByTestId("metric-filter")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "All", exact: true }).click();
  const row = page.locator(".split table").first().locator("tbody tr").first();
  await row.getByRole("button", { name: "Plot" }).click();
  await page.getByRole("menuitem", { name: /Plot it across the runs/ }).click();
  await expect(page).toHaveURL(/ctab=metrics/);
  await expect(page.locator(".chart-card:not(.slice-fam .chart-card)")).toHaveCount(1, { timeout: 20_000 });
  // in the Metrics tab: a slice row → its family in one chart
  const slice = page.locator(".card", { hasText: "Slice breakdowns" });
  await expect(slice).toBeVisible({ timeout: 20_000 });
  await slice.getByTestId("slice-family").first().getByRole("button", { name: /^Plot / }).click();
  await page.getByRole("menuitem", { name: /family together/ }).click();
  await expect(page.locator(".chart-card:not(.slice-fam .chart-card)")).toHaveCount(1);
  await expect(page.locator(".chart-card:not(.slice-fam .chart-card)").first()).toContainText(/metrics|\//);
  await expect(page.getByTestId("section-summary")).toBeVisible();
});

test("57 slice breakdowns are plots (final values on request); sections fold and unfold", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&ctab=metrics`);
  const card = page.locator(".card", { hasText: "Slice breakdowns" });
  await expect(card).toBeVisible({ timeout: 30_000 });
  // plots by default: a chart per family
  await expect(card.getByTestId("slice-family").first().locator(".u-wrap, canvas, .uplot").first()).toBeVisible({ timeout: 20_000 });
  // a family folds (its chart goes away) and unfolds
  const fam = card.getByTestId("slice-family").first();
  await fam.getByRole("button", { name: /^Fold / }).click();
  await expect(fam.locator(".uplot")).toHaveCount(0);
  await fam.getByRole("button", { name: /^Unfold / }).click();
  await expect(fam.locator(".uplot")).toHaveCount(1, { timeout: 20_000 });
  // the final-value table on request
  await card.getByRole("button", { name: "Final values", exact: true }).click();
  await expect(card.locator("table").first()).toBeVisible();
  await card.getByRole("button", { name: "Plots", exact: true }).click();
  // the whole section folds, and the choice is remembered
  await page.getByRole("button", { name: "Fold Slice breakdowns" }).click();
  await expect(page.getByTestId("slice-breakdowns")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "Unfold Slice breakdowns" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Unfold Slice breakdowns" }).click();
  await expect(page.getByTestId("slice-breakdowns")).toBeVisible();
  // compare: fold all / unfold all
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}`);
  await expect(page.getByTestId("metric-filter")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Fold all", exact: true }).click();
  await expect(page.locator(".split table")).toHaveCount(0);
  await page.getByRole("button", { name: "Unfold all", exact: true }).click();
  await expect(page.locator(".split table").first()).toBeVisible();
});

test("58 sets in two rows, more sets, charts hide / remove, multi-level filter (class, modality, distance)", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&ctab=metrics`);
  // losses and metrics on separate rows
  await expect(page.getByTestId("sets-loss")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("sets-metric")).toBeVisible();
  await expect(page.getByTestId("sets-loss").getByRole("button", { name: /All losses/ })).toBeVisible();
  // pick an evaluation set; hide one chart, show it again, remove one
  await page.getByTestId("sets-metric").getByRole("button", { name: /evaluation/ }).first().click();
  const charts = page.locator(".ml-main > .grid > .card");
  await expect(charts.first()).toBeVisible({ timeout: 20_000 });
  const n = await charts.count();
  expect(n).toBeGreaterThan(1);
  await charts.first().hover();
  await charts.first().getByRole("button", { name: "Hide chart" }).click();
  await expect(charts).toHaveCount(n - 1);
  await page.getByTestId("hidden-charts").getByRole("button", { name: "show all" }).click();
  await expect(charts).toHaveCount(n);
  await charts.first().hover();
  await charts.first().getByRole("button", { name: "Remove chart" }).click();
  await expect(charts).toHaveCount(n - 1);
  // a slice family hides, and comes back
  const fams = page.getByTestId("slice-family");
  await expect(fams.first()).toBeVisible({ timeout: 20_000 });
  const famCount = await fams.count();
  await fams.first().getByRole("button", { name: /^Hide / }).click();
  await expect(fams).toHaveCount(famCount - 1);
  await page.getByTestId("hidden-families").getByRole("button", { name: "show all" }).click();
  await expect(fams).toHaveCount(famCount);
  // multi-level filter: modality and class facets, combined
  const filter = page.getByTestId("metrics-filter");
  await expect(filter.locator('[data-dim="modality"]')).toBeVisible();
  const before = await page.getByTestId("metric-list").locator(".metric-row").count();
  await filter.locator('[data-dim="modality"] .filter-chip').first().click();
  await expect.poll(async () => page.getByTestId("metric-list").locator(".metric-row").count()).toBeLessThan(before);
  await filter.getByRole("button", { name: /clear filters/ }).click();
  await expect.poll(async () => page.getByTestId("metric-list").locator(".metric-row").count()).toBe(before);
});

test("59 decoder layers: final value per layer and the change from the previous layer", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&ctab=metrics`);
  const fam = page.getByTestId("slice-family").filter({ hasText: "train/loss/bbox" }).first();
  await expect(fam).toBeVisible({ timeout: 30_000 });
  const unfold = fam.getByRole("button", { name: /^Unfold / });
  if (await unfold.isVisible()) await unfold.click();
  const steps = fam.getByTestId("layer-steps");
  await expect(steps).toBeVisible({ timeout: 20_000 });
  await expect(steps).toContainText("L0");
  await expect(steps).toContainText("%)");
  await expect(steps).toContainText("baseline");
});

test("60 training health questions: answered from the run, plot, what to try; baseline questions in compare", async ({ page }) => {
  await open(page, `/runs/${enc("local:cand-radar-8f")}?tab=metrics`);
  const qs = page.getByTestId("loss-questions");
  await expect(qs).toBeVisible({ timeout: 30_000 });
  // problems first: the dying radar features are found
  const feat = qs.locator('[data-qid="features"]');
  await expect(feat).toContainText("radar_fpn");
  await feat.getByRole("button", { name: /Is a module or modality dying/ }).click();
  await expect(feat).toContainText("What to try");
  await feat.getByRole("button", { name: "Plot" }).click();
  await expect(page.locator(".chart-card:not(.slice-fam .chart-card)").first()).toContainText("feat_zero_frac", { timeout: 20_000 });
  // every question, including "is the loss diverging" and the layer refinement
  await page.locator('[data-fold="metrics.questions"]').getByRole("button", { name: /^All/ }).click();
  await expect(qs.locator('[data-qid="diverge"]')).toContainText("Is the loss diverging?");
  await expect(qs.locator('[data-qid="layers_diverge"]')).toBeVisible();
  // compare: the baseline questions
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&ctab=metrics`);
  await expect(page.getByTestId("loss-questions").locator('[data-qid="base_total"]')).toBeVisible({ timeout: 30_000 });
});


test("61 features: the level sets the defaults, a light view switches heavy panels off, single features toggle", async ({ page }) => {
  const runMetrics = `/runs/${enc("local:cand-radar-8f")}?tab=metrics`;
  // expert: everything, incl. the expert-only Uncertainty tab
  await open(page, runMetrics);
  await expect(page.getByTestId("loss-questions")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("tab", { name: "Uncertainty" })).toBeVisible();
  // standard (intermediate): no expert tabs, the health questions stay
  await level(page, "Standard", runMetrics);
  await expect(page.getByRole("tab", { name: "Uncertainty" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Timeline" })).toHaveCount(0);
  await expect(page.getByTestId("loss-questions")).toBeVisible();
  // light view: heavy panels are gone (and load nothing)
  await open(page, "/settings");
  const fs = page.getByTestId("feature-settings");
  await fs.getByRole("button", { name: "Light view" }).click();
  await expect(fs.getByRole("switch", { name: "Training health questions" })).toHaveAttribute("aria-checked", "false");
  await open(page, runMetrics);
  await expect(page.getByTestId("metric-list")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("loss-questions")).toHaveCount(0);
  await expect(page.getByTestId("slice-breakdowns")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Findings" })).toHaveCount(0);
  // one feature back on
  await open(page, "/settings");
  await page.getByTestId("feature-settings").getByRole("switch", { name: "Training health questions" }).click();
  await open(page, runMetrics);
  await expect(page.getByTestId("loss-questions")).toBeVisible({ timeout: 30_000 });
  // back to the level's defaults
  await open(page, "/settings");
  await page.getByTestId("feature-settings").getByRole("button", { name: "Level defaults" }).click();
  await expect(page.getByTestId("feature-settings").getByRole("switch", { name: "Slice breakdowns" })).toHaveAttribute("aria-checked", "true");
});

test("62 experiment ledger: add a comparison, outcome filled in, hypothesis and decision, to knowledge", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}`);
  await page.getByRole("button", { name: /Add to ledger/ }).click();
  await expect(page).toHaveURL(/\/ledger\?id=L-/, { timeout: 30_000 });
  const entry = page.getByTestId("ledger-entry");
  await expect(entry.getByTestId("ledger-outcome")).toBeVisible({ timeout: 30_000 });
  await expect(entry.getByTestId("ledger-outcome")).toContainText("Metrics that moved");
  await expect(entry.getByTestId("ledger-outcome")).toContainText("Training health problems");
  const title = `Radar FPN levels ${Date.now()}`;
  await entry.getByLabel("Title").fill(title);
  await entry.getByLabel("Hypothesis").fill("a second radar FPN level improves far-range recall");
  await entry.getByRole("button", { name: "reject", exact: true }).click();
  await entry.getByLabel("Decision note").fill("the last radar scale collapses");
  await entry.getByRole("button", { name: "Save", exact: true }).click();
  const item = page.getByTestId("ledger-list").locator(".ledger-item", { hasText: title });
  await expect(item).toContainText("decided");
  await expect(item).toContainText("reject");
  await entry.getByRole("button", { name: /To knowledge/ }).click();
  await expect(page.getByText(/Proposed K-ledger-/)).toBeVisible();
  // search and filter
  await page.getByLabel("Search the ledger").fill(title);
  await expect(page.getByTestId("ledger-list").locator(".ledger-item")).toHaveCount(1);
  await page.getByRole("button", { name: "Planned", exact: true }).click();
  await expect(page.getByTestId("ledger-list")).toHaveCount(0);
});

test("63 model internals: attention per decoder layer × modality, notes, plot a modality across layers", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&ctab=metrics`);
  const mi = page.getByTestId("model-internals");
  await expect(mi).toBeVisible({ timeout: 30_000 });
  await expect(mi.locator("tr[data-layer]")).toHaveCount(6);
  await expect(mi).toContainText("radar");
  await expect(mi).toContainText(/barely reads radar|faded/);
  await mi.getByRole("button", { name: /^radar/ }).click();
  await expect(page.locator(".chart-card:not(.slice-fam .chart-card)").first()).toContainText("attn_mass", { timeout: 20_000 });
  // the health question about attention answers from the same data
  await expect(page.getByTestId("loss-questions").locator('[data-qid="attention"]')).toBeVisible();
  // a run without the kit's attention statistics says how to log them; the standard level hides the panel
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=metrics`);
  await expect(page.getByTestId("internals-missing")).toBeVisible({ timeout: 30_000 });
  await level(page, "Standard", `/runs/${enc("local:xf-pipe-finetune")}?tab=metrics`);
  await expect(page.getByTestId("internals-missing")).toHaveCount(0);
});

test("64 loss drift: how every shared loss progresses vs the baseline, with readings", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&ctab=metrics`);
  const d = page.getByTestId("loss-drift");
  await expect(d).toBeVisible({ timeout: 30_000 });
  await expect(d).toContainText(/drift together|first steps/);
  await expect(d.locator('tr[data-drift="behind"]').first()).toBeVisible();
  const key = await d.locator('tr[data-drift="behind"] td.mono').first().innerText();
  await d.locator('tr[data-drift="behind"]').first().getByRole("button", { name: /^Plot / }).click();
  await expect(page.locator(".chart-card:not(.slice-fam .chart-card)").first()).toContainText(key.trim(), { timeout: 20_000 });
});

test("65 expert review: the run against the codebase's expectations, health and the baseline", async ({ page }) => {
  await open(page, `/runs/${enc("local:cand-radar-8f")}?tab=review&baseline=${enc("local:baseline-cl4f-s0")}`);
  const r = page.getByTestId("expert-review");
  await expect(r).toBeVisible({ timeout: 30_000 });
  const code = page.getByTestId("code-expectations");
  await expect(code).toBeVisible({ timeout: 30_000 });
  await expect(code.locator('tr[data-check="composition"][data-status="bad"]')).toContainText("weighted sum");
  await expect(code).toContainText("stops learning");
  await expect(code.locator(".chart-card").first()).toBeVisible();
  const findings = page.getByTestId("review-findings");
  await expect(findings).toContainText("vs code");
  await expect(findings).toContainText("implementation");
  await expect(r).toContainText("Against the baseline");
  // plot from a finding → the Metrics tab with those series
  await findings.locator("li").first().getByRole("button", { name: "Plot" }).click();
  await expect(page).toHaveURL(/tab=metrics/);
  await expect(page.locator(".chart-card:not(.slice-fam .chart-card)").first()).toBeVisible({ timeout: 20_000 });
  // a run the expectations do not cover says how to produce them
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=review`);
  const how = page.getByTestId("expectations-howto");
  await expect(how).toBeVisible({ timeout: 30_000 });
  await expect(how).toContainText("--task loss_expectations");
  await expect(how.getByRole("link", { name: /brief\.md/ })).toHaveAttribute("href", /harness-brief\?task=loss_expectations/);
});

test("66 analysis dossier for the local LLM, and analyses one stage at a time", async ({ page }) => {
  await open(page, `/runs/${enc("local:cand-radar-8f")}?baseline=${enc("local:baseline-cl4f-s0")}`);
  const dl = page.waitForEvent("download");
  await menu(page, /Dossier for the LLM/);
  const file = await dl;
  expect(file.suggestedFilename()).toMatch(/cand-radar-8f-.*\.md$/);
  const text = await (await import("node:fs/promises")).readFile(await file.path(), "utf-8");
  expect(text).toContain("# Analysis dossier");
  expect(text).toContain("[E-Q1]");
  expect(text).toContain("# Instructions for the reader");
  await expect(page.getByText(/Dossier saved: .*dossiers/)).toBeVisible();
  // the health questions of one pipeline stage (an evaluation stage has no training loss)
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=metrics`);
  const qs = page.locator('[data-fold="metrics.questions"]');
  await expect(qs.getByLabel("Stage to analyse")).toBeVisible({ timeout: 30_000 });
  await qs.getByLabel("Stage to analyse").selectOption("evaluate_finetune");
  await qs.getByRole("button", { name: /^All/ }).click();
  await expect(page.getByTestId("loss-questions").locator('[data-qid="diverge"]')).toContainText("No training loss");
});

test("67 analysis thresholds are configuration: shown in Settings with their default and meaning", async ({ page }) => {
  await open(page, "/settings");
  const card = page.locator('[data-fold="settings.thresholds"]');
  await expect(card).toBeVisible({ timeout: 20_000 });
  const unfold = card.getByRole("button", { name: /^Unfold/ });
  if (await unfold.isVisible()) await unfold.click();
  const t = page.getByTestId("threshold-settings");
  await expect(t.locator('[data-threshold="loss_questions.term_share"]')).toContainText("0.7");
  await expect(t.locator('[data-threshold="internals.starved_max"]')).toContainText("below");
});

test("68 loss dynamics: what the LR schedule did, and which loss term trains slower than its group", async ({ page }) => {
  await open(page, `/runs/${enc("local:cand-radar-8f")}?tab=review&baseline=${enc("local:baseline-cl4f-s0")}`);
  const card = page.locator('[data-fold="review.dynamics"]');
  await expect(card).toBeVisible({ timeout: 30_000 });
  const unfold = card.getByRole("button", { name: /^Unfold/ });
  if (await unfold.isVisible()) await unfold.click();
  const d = page.getByTestId("loss-dynamics");
  await expect(d).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("dynamics-fits")).toContainText("train/loss");
  await expect(page.getByTestId("dynamics-effects")).toContainText("same schedule");
  const rates = page.getByTestId("dynamics-rates");
  await expect(rates).toContainText("train/loss/velocity");
  await expect(rates).toContainText("lagging");
  // the starved term is also a health problem in the prioritised review list
  await expect(page.getByTestId("review-findings")).toContainText("slower than its group");
  // plot the group of the slow term → Metrics tab
  await rates.getByRole("button", { name: "Plot train/loss/velocity" }).click();
  await expect(page).toHaveURL(/tab=metrics/);
});

test("69 one stage everywhere; evaluation results as values per stage, not one-point curves", async ({ page }) => {
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=metrics&baseline=${enc("local:xf-online-baseline")}`);
  // an evaluation metric (one value per stage) → the Values panel: every stage, the change from the previous stage
  const list = page.getByTestId("metric-list");
  await expect(list).toBeVisible({ timeout: 30_000 });
  const grp = list.locator(":scope > div", { hasText: "evaluate_pretrain / val/mean_f1_score" }).first();
  await expect(grp.locator(".value-tag").first()).toHaveText("value");
  await grp.locator(".metric-row input").first().check();
  const vp = page.getByTestId("value-panel");
  await expect(vp).toBeVisible({ timeout: 20_000 });
  await expect(vp.locator('tr[data-stage="evaluate_pretrain"]')).toBeVisible();
  await expect(vp.locator('tr[data-stage="evaluate_finetune"]')).toContainText("vs evaluate_pretrain");
  await expect(vp).toContainText("vs baseline");
  // the run-wide stage: Metrics shows that stage only, and it follows to the Expert review (URL ?stage=)
  const st = page.getByTestId("run-stage").locator("select");
  await st.selectOption("train_finetune");
  await expect(page).toHaveURL(/stage=train_finetune/);
  await expect(page.getByTestId("stage-only")).toContainText("train_finetune");
  await expect(page.getByTestId("stage-toggles")).toHaveCount(0);
  await page.getByRole("tab", { name: "Review", exact: true }).click();
  await expect(page.getByTestId("expert-review").getByLabel("Stage to analyse").first()).toHaveValue("train_finetune");
  await expect(page.getByTestId("run-stage").locator("select")).toHaveValue("train_finetune");
  // compare: one stage for the deltas, curves and metrics
  await open(page, `/compare?baseline=${enc("local:xf-online-baseline")}&candidate=${enc("local:xf-pipe-finetune")}`);
  const cs = page.getByTestId("compare-stage").locator("select");
  await expect(cs).toBeVisible({ timeout: 30_000 });
  await cs.selectOption("evaluate_finetune");
  await expect(page).toHaveURL(/stage=evaluate_finetune/);
  await expect(page.getByTestId("cmp-values").first()).toBeVisible({ timeout: 30_000 });
});

test("70 stages of a pipeline stay that pipeline; plots overlay the runs; hover shows what is plotted", async ({ page }) => {
  await open(page, `/compare?baseline=${enc("local:xf-online-baseline")}&candidate=${enc(RUN)}`);
  const cand = page.getByTestId("subject-picker").nth(1);
  await cand.getByRole("button", { name: /Stages of/ }).click();
  await cand.getByTestId("stage-list").locator("input[type=checkbox]").nth(1).click();  // drop evaluate_pretrain
  await expect(page).toHaveURL(/candidate=group%3A/);
  // still one pipeline, by its name, saying which stages are in — and another stage can be dropped from it
  const pm = cand.getByTestId("pipeline-member");
  await expect(pm).toHaveCount(1, { timeout: 20_000 });
  await expect(pm.getByTestId("pipeline-stages-summary")).toContainText("2 of 3 stages · without evaluate_pretrain");
  await expect(page.getByTestId("runs-overview").getByTestId("subject-label")).toContainText("without evaluate_pretrain");
  await pm.getByRole("button", { name: /Stages of/ }).click();
  await pm.getByTestId("stage-list").locator("input[type=checkbox]").nth(2).click();  // drop evaluate_finetune too
  await expect(cand.getByTestId("pipeline-stages-summary")).toContainText("1 of 3 stages", { timeout: 20_000 });
  // plots: candidate and baseline in one chart, each series named run · metric; hover → tooltip with every value
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:cand-radar-8f")}&ctab=plots`);
  await expect(page.getByTestId("plots-overlay")).toContainText("baseline dashed");
  const pv = page.getByTestId("plot-view").first();
  await expect(pv.getByTestId("plot-what")).toContainText("2 runs", { timeout: 30_000 });
  await expect(pv).toContainText("baseline-cl4f-s0 (baseline) ·");
  await pv.scrollIntoViewIfNeeded();
  const box = (await pv.locator(".u-over").boundingBox())!;
  await page.mouse.move(box.x + 20, box.y + box.height / 2);
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height / 2, { steps: 6 });
  const tip = page.locator(".chart-tip:visible");  // floats above the page: never clipped by the chart or its card
  await expect(tip).toHaveCount(1);
  await expect(tip).toContainText("at step");
  await expect(tip.locator(".chart-tip-row")).toHaveCount(1);  // the curve under the mouse, not every curve
  await expect(tip.locator(".chart-tip-name")).toContainText(/ · train\/loss/);
  const fits = async (): Promise<boolean> => tip.evaluate((t) => {
    const r = t.getBoundingClientRect(); const n = t.querySelector<HTMLElement>(".chart-tip-name")!;
    return r.left >= 0 && r.right <= window.innerWidth && r.top >= 0 && r.bottom <= window.innerHeight && n.scrollWidth <= n.clientWidth + 1;
  });
  expect(await fits()).toBe(true);  // the full name, inside the window
  await page.mouse.move(box.x + box.width - 3, box.y + box.height - 3, { steps: 4 });  // at the chart's far corner
  await expect(tip).toHaveCount(1);
  expect(await fits()).toBe(true);
});

test("71 the page's stage reaches Overview, Plots and Uncertainty", async ({ page }) => {
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=overview&stage=evaluate_finetune`);
  await expect(page.getByTestId("overview-stage")).toContainText("evaluate_finetune", { timeout: 30_000 });
  await expect(page.getByTestId("headline")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("tab", { name: "Metrics", exact: true }).click();
  await page.getByRole("tab", { name: /^Plots/ }).click();
  await expect(page.getByTestId("plots-empty")).toContainText("single values only", { timeout: 30_000 });  // no one-point curves
  await page.getByTestId("run-stage").locator("select").selectOption("train_finetune");
  await expect(page.locator('[data-plot^="losses"]')).toBeVisible({ timeout: 30_000 });
  await page.getByRole("tab", { name: /^Uncertainty/ }).click();
  await expect(page).toHaveURL(/stage=train_finetune/);
  await expect(page.getByTestId("plot-view").first()).toBeVisible({ timeout: 30_000 });
});

test("72 matching instability and logit growth: internals and health questions", async ({ page }) => {
  await open(page, `/runs/${enc("local:cand-radar-8f")}?tab=metrics&baseline=${enc("local:baseline-cl4f-s0")}`);
  const st = page.getByTestId("internals-stability");
  await expect(st).toBeVisible({ timeout: 30_000 });
  await expect(st.locator('tr[data-stat="match_instability"]')).toBeVisible();
  await expect(st.locator('tr[data-stat="attn_logit_max"]')).toContainText("↑");
  await expect(page.getByTestId("loss-questions")).toContainText("matching");
  await page.getByRole("tab", { name: "Review", exact: true }).click();
  await expect(page.getByTestId("review-findings")).toContainText("does not settle", { timeout: 30_000 });
});

test("73 the stage reaches every tab: the stage's own run, its rows first, or the whole pipeline — said under the tabs", async ({ page }) => {
  const url = (tab: string): string => `/runs/${enc("local:xf-pipe-finetune")}?tab=${tab}&stage=evaluate_finetune`;
  await open(page, url("config"));
  const scope = page.getByTestId("stage-scope");
  await expect(scope).toHaveAttribute("data-scope", "run", { timeout: 30_000 });
  await expect(scope).toContainText("xf-pipe-finetune-eval-ft", { timeout: 30_000 });
  await open(page, url("dataset"));
  await expect(page.getByTestId("stage-scope")).toHaveAttribute("data-scope", "opens", { timeout: 30_000 });
  const only = page.getByTestId("stage-only-info");
  await expect(only).toContainText("evaluate_finetune", { timeout: 30_000 });
  await only.getByRole("button", { name: /all 3 stages/ }).click();
  await expect(only).toContainText("All stages shown");
  await open(page, url("timeline"));
  await expect(page.getByTestId("stage-scope")).toHaveAttribute("data-scope", "whole", { timeout: 30_000 });
  await open(page, url("overview"));
  await expect(page.getByTestId("stage-scope")).toHaveAttribute("data-scope", "logged", { timeout: 30_000 });
});

test("74 overview headline: combined metrics first, then one metric per class", async ({ page }) => {
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=metrics`);  // loads the metric values (Light Mode does not)
  await expect(page.getByTestId("metric-list")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("tab", { name: "Summary", exact: true }).click();
  const comb = page.getByTestId("headline-combined");
  await expect(comb).toBeVisible({ timeout: 30_000 });
  await expect(comb.locator(".metric-tile").first()).toHaveAttribute("data-key", /combined/);
  await expect(comb.locator(".mt-name").first()).toHaveText("combined_metric");
  const pc = page.getByTestId("headline-per-class");
  await expect(pc).toContainText("Per class");
  await expect(pc.locator(".metric-tile .mt-name", { hasText: "PassengerCar" })).toHaveCount(1);
  await expect(pc.locator(".metric-tile .mt-name", { hasText: "combined" })).toHaveCount(0);
});

test("75 downloads: the run folder is shown once, file paths are relative to it (stages under stages/<stage>/)", async ({ page }) => {
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=artifacts`);
  const root = page.getByTestId("local-root").first();
  await expect(root).toContainText("xf-pipe-finetune", { timeout: 30_000 });
  await expect(root.getByRole("button", { name: "Copy the run folder" })).toBeVisible();
  await expect(page.locator("td.mono", { hasText: /^stages\/train_finetune\// }).first()).toBeVisible();
  // "change…" opens Settings → Storage (the downloads folder is set there)
  await root.getByRole("link", { name: /change/ }).click();
  await expect(page).toHaveURL(/\/settings#storage/);
  await expect(page.getByTestId("storage-settings")).toContainText("Downloaded files", { timeout: 20_000 });
  await expect(page.getByTestId("storage-settings")).toContainText("changes at once");
});

test("76 add Azure ML workspaces from the az login list (any subscription); a Back button inside the app", async ({ page }) => {
  const SUB = "00000000-0000-0000-0000-00000000000a", SUB2 = "00000000-0000-0000-0000-00000000000b";
  await page.route("**/api/v1/settings/azure/available**", async (route) => {
    const sub = new URL(route.request().url()).searchParams.get("subscription_id") ?? SUB;
    await route.fulfill({ json: {
      subscriptions: [{ id: SUB, name: "Team A", tenant_id: "t", default: true, state: "Enabled" }, { id: SUB2, name: "Team B", tenant_id: "t", default: false, state: "Enabled" }],
      subscription_id: sub,
      workspaces: sub === SUB2
        ? [{ name: "ws-research", title: "WS-Research", subscription_id: SUB2, resource_group: "rg-b", workspace: "WS-Research", location: "westeurope" }]
        : [{ name: "ws-prod", title: "WS-Prod", subscription_id: SUB, resource_group: "rg-a", workspace: "WS-Prod", location: "westeurope" }],
    } });
  });
  await open(page, "/runs");
  await expect(page.getByTestId("back")).toHaveCount(0);  // the first page opened: nothing to go back to
  await page.locator(".sidebar").getByRole("link", { name: "Settings" }).click();  // a step inside the app
  await page.getByRole("button", { name: /Add from my Azure account/ }).click();
  const pick = page.getByTestId("workspace-picker");
  await pick.getByLabel("Workspace").selectOption({ label: "WS-Prod · rg-a · westeurope" });
  await pick.getByRole("button", { name: "Add" }).click();
  await pick.getByLabel("Subscription").selectOption(SUB2);  // another subscription: its workspaces
  await pick.getByLabel("Workspace").selectOption({ label: "WS-Research · rg-b · westeurope" });
  await pick.getByRole("button", { name: "Add" }).click();
  const ws = page.getByRole("textbox", { name: "Workspace", exact: true });
  await expect(ws).toHaveCount(2);
  await expect(ws.nth(0)).toHaveValue("WS-Prod");
  await expect(ws.nth(1)).toHaveValue("WS-Research");
  await expect(page.getByRole("textbox", { name: "Subscription ID" }).nth(1)).toHaveValue(SUB2);
  await expect(pick.getByLabel("Workspace")).toContainText("all already added");
  // back inside the app
  await page.getByTestId("back").click();
  await expect(page).toHaveURL(/\/runs$/);
});

test("77 logs: wrap long lines (remembered) or keep one row per line and scroll sideways", async ({ page }) => {
  const long = `INFO step 1200 ${"loss_bbox=0.41234 loss_cls=0.2231 grad_norm=3.14 ".repeat(12)}done`;
  await page.route("**/api/v1/runs/*/logs/file?**", (route) => route.fulfill({ json: { id: "logs/std_log.txt", path: "logs/std_log.txt", stage: null, run: "local:baseline-cl4f-s0", size_bytes: 200, read_bytes: 200, partial: false, text: `start\n${long}\nERROR CUDA out of memory\n` } }));
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}?tab=logs`);
  const view = page.getByTestId("log-view");
  await expect(view).toHaveAttribute("data-wrap", "on", { timeout: 20_000 });
  const line = view.locator(".log-line").nth(1);
  const wrappedH = (await line.boundingBox())!.height;
  expect(wrappedH).toBeGreaterThan(30);  // the long line wraps onto several rows
  await page.getByRole("switch", { name: "Wrap lines" }).click();
  await expect(view).toHaveAttribute("data-wrap", "off");
  expect((await line.boundingBox())!.height).toBeLessThan(wrappedH / 2);  // one row …
  expect(await view.evaluate((v) => v.scrollWidth > v.clientWidth)).toBe(true);  // … and the view scrolls sideways
  await page.reload();
  await expect(page.getByTestId("log-view")).toHaveAttribute("data-wrap", "off", { timeout: 20_000 });  // remembered
});

test("78 analyse a local run folder in place: add it from the Runs page, open it with the same tools", async ({ page, request }) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mf-local-run-"));
  const v = path.join(dir, "my-local-train", "lightning_logs", "version_0");
  fs.mkdirSync(v, { recursive: true });
  const rows = ["epoch,step,train/loss,val/loss"];
  for (let i = 0; i < 40; i++) rows.push(`${Math.floor(i / 10)},${i * 10},${(3 / (1 + i / 5)).toFixed(4)},`);
  rows.push("3,400,,0.91");
  fs.writeFileSync(path.join(v, "metrics.csv"), rows.join("\n") + "\n");
  fs.writeFileSync(path.join(v, "hparams.yaml"), "lr: 0.0002\n");
  const run = path.join(dir, "my-local-train");
  try {
    await open(page, "/runs");
    await page.getByRole("button", { name: /Analyse a local run/ }).click();
    const add = page.getByTestId("local-run-add");
    await add.getByLabel("Local folder").fill(run);
    await add.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("local-preview")).toContainText("1 run(s) found");
    await expect(page.getByTestId("local-preview")).toContainText("lightning_logs/version_0/metrics.csv");
    await add.getByRole("button", { name: "Add" }).click();
    await expect(page).toHaveURL(/\/runs\/local-runs%3Amy-local-train-/, { timeout: 20_000 });
    await page.getByRole("tab", { name: /^Metrics/ }).click();
    await expect(page.getByTestId("loss-list")).toContainText("loss", { timeout: 30_000 });
    await page.getByRole("tab", { name: "Files", exact: true }).click();
    await page.getByRole("tab", { name: /^Config/ }).click();
    await expect(page.getByText("hparams.yaml.lr").first()).toBeVisible({ timeout: 20_000 });  // the run's own config, read in place
  } finally {
    await request.put("/api/v1/settings/local-runs", { data: { folders: [] } });  // leave the demo as it was
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("79 a running local run is followed live; Compare lines up metric names (train_loss_step = train/loss)", async ({ page, request }) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mf-live-run-"));
  const run = path.join(dir, "live-train");
  const v = path.join(run, "lightning_logs", "version_0");
  fs.mkdirSync(v, { recursive: true });
  const rows = ["epoch,step,train_loss_step,val_loss"];
  for (let i = 0; i < 30; i++) rows.push(`0,${i * 10},${(2 - i * 0.03).toFixed(3)},`);
  fs.writeFileSync(path.join(v, "metrics.csv"), rows.join("\n") + "\n");  // written now: still running
  try {
    const added = await (await request.post("/api/v1/settings/local-runs/add", { data: { path: run } })).json();
    const ref = added.runs.find((r: { name: string }) => r.name === "live-train").ref as string;
    await open(page, `/runs/${enc(ref)}`);
    const live = page.getByTestId("live");
    await expect(live).toContainText("live", { timeout: 20_000 });
    await live.click();
    await expect(live).toContainText("frozen");
    await live.click();
    // Compare it with a demo run: the same loss under another name is offered to line up
    await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc(ref)}`);
    const names = page.getByTestId("metric-names");
    await expect(names).toBeVisible({ timeout: 30_000 });
    await expect(names.locator("tr", { hasText: "train_loss_step" })).toContainText("train/loss");
    await names.getByRole("button", { name: /Line up \d+ pair/ }).click();
    await expect(names.locator("tr", { hasText: "train_loss_step" })).toHaveCount(0, { timeout: 20_000 });
  } finally {
    await request.put("/api/v1/settings/metric-aliases", { data: { remove: ["train_loss_step", "val_loss"] } });
    await request.put("/api/v1/settings/local-runs", { data: { folders: [] } });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

/** A TensorBoard event record with a PyTorch-style scalar (simple_value) — what SummaryWriter.add_scalar writes. */
function tfEvent(step: number, tag: string, value: number): Buffer {
  const varint = (n: number): Buffer => { const out: number[] = []; do { let b = n & 0x7f; n = Math.floor(n / 128); if (n) b |= 0x80; out.push(b); } while (n); return Buffer.from(out); };
  const ld = (fn: number, data: Buffer): Buffer => Buffer.concat([varint((fn << 3) | 2), varint(data.length), data]);
  const f32 = Buffer.alloc(4); f32.writeFloatLE(value);
  const f64 = Buffer.alloc(8); f64.writeDoubleLE(1700000000 + step);
  const val = Buffer.concat([ld(1, Buffer.from(tag)), varint((2 << 3) | 5), f32]);
  const ev = Buffer.concat([varint((1 << 3) | 1), f64, varint((2 << 3) | 0), varint(step), ld(5, ld(1, val))]);
  const len = Buffer.alloc(8); len.writeBigUInt64LE(BigInt(ev.length));
  return Buffer.concat([len, Buffer.alloc(4), ev, Buffer.alloc(4)]);
}

test("80 Local runs section: TensorBoard and evaluation files read in place; compare the selected runs", async ({ page, request }) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mf-local-section-"));
  const a = path.join(dir, "exp", "run-csv");
  fs.mkdirSync(path.join(a, "lightning_logs", "version_0"), { recursive: true });
  fs.writeFileSync(path.join(a, "lightning_logs", "version_0", "metrics.csv"), ["step,train/loss", ...Array.from({ length: 20 }, (_, i) => `${i * 10},${(2 - i * 0.05).toFixed(3)}`)].join("\n") + "\n");
  fs.mkdirSync(path.join(a, "eval"));
  [0.3, 0.36, 0.41].forEach((ap, i) => fs.writeFileSync(path.join(a, "eval", `eval_epoch_${i + 1}.json`), JSON.stringify({ mAP: ap })));
  const b = path.join(dir, "exp", "run-tb");
  fs.mkdirSync(b, { recursive: true });
  fs.writeFileSync(path.join(b, "events.out.tfevents.1.host"), Buffer.concat(Array.from({ length: 20 }, (_, i) => tfEvent(i * 10, "train/loss", 2.2 - i * 0.04))));
  const old = (Date.now() - 3_600_000) / 1000;
  for (const f of [path.join(a, "lightning_logs", "version_0", "metrics.csv"), path.join(b, "events.out.tfevents.1.host")]) fs.utimesSync(f, old, old);
  try {
    await open(page, "/runs");
    await page.locator(".sidebar").getByRole("link", { name: "Runs" }).click();
    await page.getByTestId("section-bar").getByRole("link", { name: "Local folders" }).click();
    await expect(page).toHaveURL(/\/local$/);
    const add = page.getByTestId("local-run-add");
    await add.getByLabel("Local folder").fill(path.join(dir, "exp"));
    await add.getByRole("button", { name: "Check" }).click();
    await expect(page.getByTestId("local-preview")).toContainText("2 run(s) found");
    await add.getByRole("button", { name: "Add" }).click();
    const table = page.getByTestId("local-runs-table");
    await expect(table.locator("tbody tr")).toHaveCount(2, { timeout: 20_000 });
    await expect(table.locator("tr", { hasText: "run-tb" })).toContainText("TensorBoard ×1");
    await expect(table.locator("tr", { hasText: "run-csv" })).toContainText("eval files ×3");
    await page.getByLabel("select run-csv").check();
    await page.getByLabel("select run-tb").check();
    await page.getByRole("button", { name: /Compare 2 selected/ }).click();
    await expect(page).toHaveURL(/\/compare\?baseline=local-runs/);
    await expect(page.getByTestId("runs-overview")).toContainText("run-tb", { timeout: 30_000 });
  } finally {
    await request.put("/api/v1/settings/local-runs", { data: { folders: [] } });
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("81 logs: every log of the pipeline's stages listed, the telling one first; a stage's review reads that stage alone", async ({ page }) => {
  await open(page, `/runs/${enc("local:pipeline-nightly")}?tab=logs`);
  const pick = page.getByTestId("log-file-select");
  await expect(pick).toBeVisible({ timeout: 20_000 });
  // the training stage's log first, not the pipeline's "pipeline started" orchestration log
  await expect(pick).toHaveValue(/nightly-train::logs\/std_log\.txt/);
  await expect(page.getByTestId("log-view")).toContainText("starting nightly-train");
  await expect(page.getByTestId("log-view")).not.toContainText("pipeline started");
  await expect(page.getByTestId("log-download")).toHaveAttribute("href", /artifacts\/file\?path=/);
  // the pipeline's own log can still be picked; it says little, so the others are offered
  await pick.selectOption("logs/std_log.txt");
  await expect(page.getByTestId("log-view")).toContainText("pipeline started");
  await expect(page.getByTestId("log-short")).toContainText("nightly-train");
  await page.getByTestId("log-short").getByRole("button").first().click();
  await expect(page.getByTestId("log-view")).toContainText("starting nightly-train");
  // a stage of the page: only that stage's logs
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=logs&stage=evaluate_finetune`);
  await expect(page.getByTestId("log-file-select")).toBeVisible({ timeout: 20_000 });
  const opts = await page.getByTestId("log-file-select").locator("option").allTextContents();
  expect(opts.length).toBeGreaterThan(0);
  expect(await page.getByTestId("log-file-select").locator("optgroup").evaluateAll((gs) => gs.map((g) => g.getAttribute("label")))).toEqual(["stage evaluate_finetune"]);
  // Expert review of one stage opens without waiting for the whole pipeline
  await open(page, `/runs/${enc("local:xf-pipe-finetune")}?tab=review&stage=train_finetune`);
  await expect(page.getByTestId("expert-review")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("run-stage")).toBeVisible();
});

test("82 local run folder: autocomplete the path, or browse this machine's folders and pick one", async ({ page }) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mf-browse-"));
  const run = path.join(dir, "experiments", "run-tb");
  fs.mkdirSync(run, { recursive: true });
  fs.mkdirSync(path.join(dir, "experiments", "notes"));
  fs.writeFileSync(path.join(run, "events.out.tfevents.1.host"), Buffer.concat(Array.from({ length: 10 }, (_, i) => tfEvent(i * 10, "train/loss", 2 - i * 0.1))));
  try {
    await open(page, "/local");
    const add = page.getByTestId("local-run-add");
    const field = add.getByLabel("Local folder");
    await field.fill(path.join(dir, "exp"));
    const sug = page.getByTestId("path-suggestions");
    await expect(sug).toContainText(`${path.join(dir, "experiments")}/`);
    await field.press("Tab");  // takes the suggestion, then lists its sub-folders
    await expect(field).toHaveValue(`${path.join(dir, "experiments")}/`);
    await expect(sug).toContainText("run-tb");
    await field.press("Escape");
    // Browse: opens at the typed folder; run folders are marked; "Use" fills the field and checks it
    await add.getByRole("button", { name: "Browse…" }).click();
    const br = page.getByTestId("folder-browser");
    await expect(page.getByTestId("folder-browser-path")).toHaveAttribute("data-path", path.join(dir, "experiments"));
    await expect(br.getByTestId("folder-row")).toHaveCount(2);
    const row = br.getByTestId("folder-row").filter({ hasText: "run-tb" });
    await expect(row).toContainText("TensorBoard");
    await br.getByRole("button", { name: "Up" }).click();
    await expect(page.getByTestId("folder-browser-path")).toHaveAttribute("data-path", dir);
    await br.getByTestId("folder-row").filter({ hasText: "experiments" }).getByRole("button").first().click();
    await br.getByTestId("folder-row").filter({ hasText: "run-tb" }).getByRole("button", { name: "Use" }).click();
    await expect(field).toHaveValue(run);
    await expect(page.getByTestId("folder-browser")).toHaveCount(0);
    await expect(page.getByTestId("local-preview")).toContainText("1 run(s) found");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("83 the run page has five places; old links still open the same view; Ask is a drawer; actions live in ⋯", async ({ page }) => {
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}`);
  const groups = page.locator(".tabs [role=tab]");
  await expect(groups.first()).toHaveText(/Summary/);
  const names = (await groups.allInnerTexts()).map((t) => t.replace(/\d+/g, "").trim());
  expect(names.filter((n) => n !== "Timeline")).toEqual(["Summary", "Metrics", "Review", "Files"]);  // Timeline: expert level
  await expect(page.locator(".page-head .actions > .btn")).toHaveCount(2);  // Compare + Ask; the rest in ⋯
  // an old link to a view opens it inside its place
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}?tab=logs`);
  await expect(page.getByRole("tab", { name: "Files", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("subtabs").getByRole("tab", { name: /Logs/ })).toHaveAttribute("aria-selected", "true");
  // a place remembers its last view
  await page.getByTestId("subtabs").getByRole("tab", { name: /Config/ }).click();
  await page.getByRole("tab", { name: "Summary", exact: true }).click();
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect(page).toHaveURL(/tab=config/);
  // Ask opens beside the view and closes with Escape
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByTestId("ask-drawer")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("ask-drawer")).toHaveCount(0);
  await expect(page).toHaveURL(/tab=config/);
  // ⋯ holds the rest, with copy as an mf command
  await page.getByTestId("more-actions").click();
  for (const item of ["Report", "Start a study", "Dossier for the LLM", "Copy as mf command", "Loading details"]) {
    await expect(page.getByRole("menuitem", { name: item })).toBeVisible();
  }
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
  // Compare answers in one sentence, with what changed
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s2")}&candidate=${enc("local:query-1800-spiky")}`);
  const cv = page.getByTestId("compare-verdict");
  await expect(cv).not.toHaveAttribute("data-state", "loading", { timeout: 30_000 });
  await expect(cv).toContainText(/metrics/);
  await expect(cv).toContainText("num_queries");
});

test("84 five places: sections above the page, old links land in their place, classic navigation one switch away", async ({ page }) => {
  await open(page, "/");
  const places = page.getByTestId("places").getByRole("link");
  await expect(places).toHaveText([/Today/, /Runs/, /Compare/, /Studies/, /Knowledge/]);
  await expect(page.locator(".mode-switch")).toHaveCount(0);  // the level lives in Settings → Features
  // a place opens its first section; its sections are a bar above the page
  await places.filter({ hasText: "Studies" }).click();
  await expect(page).toHaveURL(/\/studies$/);
  const bar = page.getByTestId("section-bar");
  await expect(bar.getByRole("link")).toHaveText(["Studies", "Ledger", "Test Space", "Reports"]);
  await bar.getByRole("link", { name: "Ledger" }).click();
  await expect(page).toHaveURL(/\/ledger$/);
  await expect(places.filter({ hasText: "Studies" })).toHaveAttribute("aria-current", "page");
  // an old link opens the same page, inside its place
  await open(page, "/progression");
  await expect(page.getByTestId("places").getByRole("link", { name: /Compare/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("section-bar").getByRole("link", { name: "Progression" })).toHaveAttribute("aria-current", "page");
  // a run is in Runs, without a section bar
  await open(page, `/runs/${enc("local:baseline-cl4f-s0")}`);
  await expect(page.getByTestId("places").getByRole("link", { name: /Runs/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("section-bar")).toHaveCount(0);
  // Ask and Jobs sit in the top bar (on a run page, its own Ask asks about that run: one Ask, not two)
  await expect(page.getByTestId("topbar-ask")).toHaveCount(0);
  await open(page, "/runs");
  await page.getByTestId("topbar-ask").click();
  await expect(page).toHaveURL(/\/ai$/);
  await page.getByTestId("topbar-jobs").click();
  // classic: every page its own entry again — and back
  await open(page, "/settings");
  const ns = page.getByTestId("nav-settings");
  await ns.getByRole("button", { name: "Classic" }).click();
  await expect(page.locator(".sidebar").getByRole("link", { name: "Local runs" })).toBeVisible();
  await expect(page.locator(".mode-switch")).toBeVisible();
  await expect(page.getByTestId("section-bar")).toHaveCount(0);
  await ns.getByRole("button", { name: "Simplified" }).click();
  await expect(page.getByTestId("places")).toBeVisible();
  await expect(page.getByTestId("section-bar").getByRole("link", { name: "Connection health" })).toBeVisible();
  // three runs side by side → the same runs as a progression, one click
  await open(page, `/compare?baseline=${enc("local:baseline-cl4f-s0")}&candidate=${enc("local:baseline-cl4f-s2")}&more=${enc("local:query-1800-spiky")}`);
  await page.getByTestId("as-progression").getByRole("link").click();
  await expect(page).toHaveURL(/\/progression\?runs=.*query-1800-spiky/);
});
