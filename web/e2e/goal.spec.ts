import { expect, test, type APIRequestContext } from "@playwright/test";

// Saving a goal changes data, so these run against the mutable fixture backend (ACTIONS_PORT).
// The follower model hasn't run in the fixture, so the preview answers that it hasn't; its numbers
// are checked only when the API returns them. The empty backend checks the server's refusal.
const ACTIONS = "http://127.0.0.1:8797";
const EMPTY_BACKEND = "http://127.0.0.1:8799";

test.describe.configure({ mode: "serial" });
test.use({ baseURL: ACTIONS });

async function json(request: APIRequestContext, path: string) {
  const response = await request.get(`${ACTIONS}${path}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test.describe("Set goal dialog", () => {
  test("validates, previews where you'd stand, and saves a follower goal", async ({ page, request }) => {
    await page.goto("/#/account");
    await page.locator("header").getByRole("button", { name: "Set goal" }).click();
    const dialog = page.getByRole("dialog", { name: "Set a goal" });
    await expect(dialog.getByText("Goal status (working, near threshold, not working) is read against this target.")).toBeVisible();
    await expect(dialog.getByRole("radio", { name: "Followers" })).toHaveAttribute("aria-checked", "true");
    const save = dialog.getByRole("button", { name: "Save goal" });
    await expect(save).toBeDisabled();

    // Per-post goals take no date.
    await dialog.getByRole("radio", { name: "48h impressions per post" }).click();
    await expect(dialog.getByLabel("By")).toHaveCount(0);
    await expect(dialog.getByText("impressions", { exact: true })).toBeVisible();
    await dialog.getByRole("radio", { name: "Followers" }).click();

    await dialog.getByLabel("Target").fill("0");
    await expect(dialog.getByText("The target must be a number above zero.")).toBeVisible();
    await expect(save).toBeDisabled();
    await dialog.getByLabel("By").fill("2020-01-01");
    await expect(dialog.getByText("Pick a date in the future.")).toBeVisible();

    // Within the follower forecast's 90-day reach from the fixture's last run, so the preview has numbers.
    const target = new Date(Date.now() + 45 * 24 * 3600 * 1000).toISOString().slice(0, 10);
    await dialog.getByLabel("By").fill(target);
    const previewed = page.waitForResponse((r) => r.url().endsWith("/api/goals/preview") && r.request().method() === "POST");
    await dialog.getByLabel("Target").fill("1,000");
    const preview = await (await previewed).json();
    const stand = dialog.getByRole("region", { name: "Where you'd stand today" });
    // Without numbers the summary says why; with them, the rows carry them.
    if (preview.median == null) await expect(stand.getByText(preview.summary, { exact: true })).toBeVisible();
    if (preview.p_goal != null) {
      await expect(stand.getByText(`${Math.round(preview.p_goal * 100)}% · ${preview.paths.toLocaleString("en-US")} simulated paths`)).toBeVisible();
      await expect(stand.getByText(new RegExp(`^${Math.round(preview.median).toLocaleString("en-US")} on .+ · 80%: `))).toBeVisible();
      const label = { working: "Working", near: "Near threshold", not_working: "Not working", no_goal: "No status yet" }[
        preview.status as "working" | "near" | "not_working" | "no_goal"
      ];
      await expect(stand.getByText(label, { exact: true })).toBeVisible();
    }
    await expect(save).toBeEnabled();
    await dialog.screenshot({ path: "e2e/.results/goal-dialog.png" });

    const saved = page.waitForResponse((r) => r.url().endsWith("/api/goals") && r.request().method() === "POST");
    await save.click();
    const body = await (await saved).json();
    expect(body.saved, body.summary).toBe(true);
    await expect(dialog).toHaveCount(0);

    const goals = await json(request, "/api/goals");
    expect(goals.current).toHaveLength(1);
    expect(goals.current[0].goal.target).toBe(1000);
    const panel = page.getByRole("region", { name: "Goal" });
    await expect(panel.getByText(/^1,000 followers by /)).toBeVisible();
    await expect(panel.getByRole("button", { name: "Edit" })).toBeVisible();
    // No run since the goal was set: the status waits for the next one.
    if (goals.current[0].status === "no_goal") {
      await expect(panel.getByText("No status yet")).toBeVisible();
      await expect(panel.getByText("The next run computes its status.")).toBeVisible();
    }

    await panel.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByRole("dialog", { name: "Set a goal" }).getByLabel("Target")).toHaveValue("1000");
    await page.keyboard.press("Escape");
  });

  test("the saved goal shows on Overview and Forecasts", async ({ page }) => {
    await page.goto("/#/overview");
    const health = page.getByRole("region", { name: "Calibration" });
    await expect(health.getByRole("meter", { name: /^Goal: 1,000 followers by / })).toBeVisible();
    await page.goto("/#/forecasts");
    await expect(page.getByRole("region", { name: "Model health" }).getByText(/^Goal: 1,000 by /)).toBeVisible();
  });

  test("the server's refusal is shown: no account on an empty database", async ({ page }) => {
    await page.goto(`${EMPTY_BACKEND}/#/overview`);
    await page.getByRole("region", { name: "Getting started" }).getByRole("button", { name: "Set goal" }).click();
    const dialog = page.getByRole("dialog", { name: "Set a goal" });
    await dialog.getByLabel("Target").fill("500");
    const saved = page.waitForResponse((r) => r.url().endsWith("/api/goals") && r.request().method() === "POST");
    await dialog.getByRole("button", { name: "Save goal" }).click();
    const body = await (await saved).json();
    expect(body.saved).toBe(false);
    await expect(dialog.getByRole("alert").filter({ hasText: body.summary })).toBeVisible();
    await expect(dialog).toBeVisible();
  });
});
