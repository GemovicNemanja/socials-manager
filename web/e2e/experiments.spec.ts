import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Experiment specs run in order against their own backend (playwright.config.ts, EXPERIMENTS_PORT):
// the fixture plus three weeks of a running E03 x E06 factorial built by the real engine and
// orchestrator (`socialmgr fixture --experiment-demo`), so readouts exist. Statistics depend on the
// model and the seed, so every number checked here is read from the API first.
const EXP = "http://127.0.0.1:8796";

test.describe.configure({ mode: "serial" });
test.use({ baseURL: EXP });

async function json(request: APIRequestContext, path: string) {
  const response = await request.get(`${EXP}${path}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

/** Fills the Register slots dialog's times and submits; returns the API's response. */
async function registerSlots(page: Page, times: string[]) {
  const dialog = page.getByRole("dialog", { name: "Register slots" });
  await expect(dialog).toBeVisible();
  for (const [i, t] of times.entries()) await dialog.getByLabel(`Slot ${i + 1} time`).fill(t);
  const response = page.waitForResponse((r) => r.url().endsWith("/api/experiments/slots") && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "Register and draw arms" }).click();
  const body = await (await response).json();
  expect(body.registered, body.summary).toBe(true);
  const done = page.getByRole("dialog", { name: "Slots registered" });
  const what = done.getByRole("region", { name: "What to post" });
  for (const slot of body.slots) await expect(what.getByText(slot.instructions, { exact: true })).toBeVisible();
  return { body, done };
}

test.describe("Experiments screen", () => {
  test("running factorial with a readout card and its posts against blind forecasts", async ({ page, request }) => {
    const list = await json(request, "/api/experiments");
    const detail = await json(request, "/api/experiments/E03");
    await page.goto("/#/experiments");
    await expect(page.getByRole("heading", { name: "Experiments" })).toBeVisible();
    await expect(page.getByText(`${list.running.length} running ·`, { exact: false })).toBeVisible();

    const table = page.getByRole("region", { name: "Experiments" });
    for (const e of list.running) {
      const row = table.getByRole("row", { name: new RegExp(e.key) });
      await expect(row).toContainText(e.effect_text);
      await expect(row).toContainText(`${e.posts_matured} / ${e.slots_assigned}`);
      await expect(row).toContainText("Running");
    }

    const readout = page.getByRole("region", { name: "E03 readout" });
    await expect(readout).toContainText(`last look ${detail.readout.look}`);
    const f = detail.readout.factors[0];
    const card = readout.getByRole("region", { name: `Factor ${f.factor}` });
    await expect(card.getByText(f.headline, { exact: true })).toBeVisible();
    await expect(card).toContainText(`${f.effect_ratio.toFixed(2)}× [${f.interval80[0].toFixed(2)}, ${f.interval80[1].toFixed(2)}]`);
    await expect(card).toContainText(`${f.p_gt_delta.toFixed(2)} · adopt at 0.80`);
    await expect(card).toContainText(f.elond);
    await expect(card.getByRole("img", { name: "Estimate at each look" })).toBeVisible();

    // Always show how the past did: each arm's posts against their frozen treatment-blind forecasts.
    const past = readout.getByRole("region", { name: "Posts against blind forecasts" });
    const p = detail.past[0];
    const cells = (c: Record<string, number>) => [c.above, c.as_expected, c.below, c.awaiting, c.lost].map(String);
    await expect(past.getByRole("row", { name: /^Treated/ }).locator("[data-cell]")).toHaveText(cells(p.treated));
    await expect(past.getByRole("row", { name: /^Control/ }).locator("[data-cell]")).toHaveText(cells(p.controls));
    await expect(readout.getByRole("region", { name: "Guardrails and health" })).toContainText("Blind forecast before the draw");
    await page.screenshot({ path: "e2e/.results/experiments.png" });
    await readout.getByRole("region", { name: "Guardrails and health" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: "e2e/.results/experiments-past.png" });
  });

  test("start a suggestion, register slots and see what to post in Next posts", async ({ page, request }) => {
    await page.goto("/#/experiments");
    await page.getByRole("tab", { name: /Suggested/ }).click();
    const suggestion = page.getByRole("region", { name: "Suggestion E07" });
    await expect(suggestion).toContainText("1.25×");
    const started = page.waitForResponse((r) => r.url().endsWith("/api/experiments") && r.request().method() === "POST");
    await suggestion.getByRole("button", { name: "Start E07" }).click();
    const result = await (await started).json();
    expect(result.done).toBe(true);
    expect(result.experiment.status).toBe("scheduled");
    const status = page.getByRole("status").filter({ hasText: "E07 is scheduled" });
    await expect(status).toBeVisible();
    await page.screenshot({ path: "e2e/.results/experiments-suggested.png" });

    await status.getByRole("button", { name: "Register slots" }).click();
    const { body, done } = await registerSlots(page, ["07:00", "11:00", "16:00"]);
    expect(body.experiments).toEqual(["E03", "E06", "E07"]);
    // A factorial: every slot carries an arm of each running factor, in plain words.
    for (const slot of body.slots) expect(slot.arms.map((a: { experiment: string }) => a.experiment)).toEqual(["E03", "E06", "E07"]);
    await page.screenshot({ path: "e2e/.results/register-slots-result.png" });
    await done.getByRole("button", { name: "Done" }).click();

    await page.getByRole("navigation", { name: "Main" }).getByText("Account", { exact: true }).click();
    const next = page.getByRole("region", { name: "Next posts" });
    await expect(next.getByText("Tomorrow", { exact: true })).toBeVisible();
    for (const slot of body.slots) {
      const row = next.locator(`[data-slot="${slot.slot}"]`);
      await expect(row).toContainText(slot.planned_local.slice(11, 16));
      await expect(row).toContainText("E03");
      const first = slot.arms[0].instruction;
      await expect(row).toContainText(first.charAt(0).toUpperCase() + first.slice(1));
    }
    const nextPosts = await json(request, "/api/next-posts");
    expect(nextPosts.drawing).toEqual(["E03", "E06", "E07"]);
  });

  test("a long-horizon proposal waits for the owner, who approves it", async ({ page, request }) => {
    await page.goto("/#/experiments");
    await page.getByRole("tab", { name: /Suggested/ }).click();
    const suggestion = page.getByRole("region", { name: "Suggestion E08" });
    await expect(suggestion).toContainText("over 8 weeks");
    await suggestion.getByRole("button", { name: "Start E08" }).click();
    await expect(page.getByRole("status").filter({ hasText: "E08 waits for your approval" })).toBeVisible();

    const approvals = page.getByRole("region", { name: "Awaiting your approval" });
    const card = approvals.getByRole("group", { name: "Start E08: Engager-peak window vs off-peak" });
    await expect(card).toContainText("over 8 weeks");
    await page.getByRole("tab", { name: /Running/ }).click();
    await expect(page.getByRole("region", { name: "Experiments" }).getByRole("row", { name: /E08/ })).toContainText("Needs approval");
    await page.screenshot({ path: "e2e/.results/experiments-approval.png" });

    const decided = page.waitForResponse((r) => /\/api\/approvals\/\d+\/approve$/.test(r.url()));
    await card.getByRole("button", { name: "Approve" }).click();
    expect((await (await decided).json()).done).toBe(true);
    await expect(page.getByRole("status").filter({ hasText: "Approved: Start E08" })).toBeVisible();
    await expect(approvals).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Experiments" }).getByRole("row", { name: /E08/ })).toContainText("Scheduled");
    expect((await json(request, "/api/experiments/E08")).experiment.status).toBe("scheduled");
  });
});

test.describe("Account: Next posts", () => {
  test("register slots from the Account screen; taken days are disabled", async ({ page, request }) => {
    await page.goto("/#/account");
    await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();
    await page.getByRole("button", { name: "Register slots" }).click();
    const dialog = page.getByRole("dialog", { name: "Register slots" });
    const before = await json(request, "/api/next-posts");
    for (const day of before.registered_days) {
      const chip = dialog.getByRole("button", { name: day, exact: true });
      if (await chip.count()) await expect(chip).toBeDisabled();
    }
    await expect(dialog.getByRole("button", { pressed: true })).toHaveCount(1);
    const { body, done } = await registerSlots(page, ["08:00", "12:00", "17:00"]);
    await done.getByRole("button", { name: "Done" }).click();

    const next = page.getByRole("region", { name: "Next posts" });
    for (const slot of body.slots) {
      const row = next.locator(`[data-slot="${slot.slot}"]`);
      await expect(row).toContainText(slot.planned_local.slice(11, 16));
      await expect(row).toContainText(/blind 80% range/);
    }
    await page.screenshot({ path: "e2e/.results/account-next-posts.png" });
  });
});
